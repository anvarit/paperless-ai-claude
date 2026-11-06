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

def test_non_latin_not_grouped():
    tags = [t(1, "ホテル", 1), t(2, "請求書", 1), t(3, "Hotel", 10)]
    res = {tag["name"]: cls for tag, cls, _ in classify(tags, set())}
    assert res["ホテル"] == "keep" and res["請求書"] == "keep"

def test_vocabulary_plan():
    from vocabulary import plan as vplan
    import re
    vocab = [("insurance", [re.compile(r"verzekering(en)?|insurance", re.I)]), ("tax", [re.compile(r"belasting(en)?|tax", re.I)])]
    tags = [dict(id=1, name="Verzekering", documents=163, **{"class": "keep"}), dict(id=2, name="insurance", documents=59, **{"class": "keep"}),
            dict(id=3, name="Belasting", documents=114, **{"class": "keep"}), dict(id=4, name="Jenever", documents=1, **{"class": "keep"}),
            dict(id=5, name="Gent", documents=20, **{"class": "keep"})]
    merges, deletes, renames, review, by_target = vplan(tags, vocab, set(), 3)
    assert {m["from_name"] for m in merges} == {"Verzekering", "Belasting"}
    assert next(m for m in merges if m["from_name"] == "Verzekering")["into"] == 2          # existing 'insurance' tag reused
    assert next(m for m in merges if m["from_name"] == "Belasting")["into"] is None        # 'tax' must be created
    assert [d["name"] for d in deletes] == ["Jenever"] and [r["name"] for r in review] == ["Gent"]

def test_doctype_plan():
    import re
    from cleanup_doctypes import plan as dplan
    vocab = [("invoice", [re.compile(r"invoice|factuur", re.I)]), ("-", [re.compile(r"unknown", re.I)])]
    types = [dict(id=1, name="Invoice", document_count=740), dict(id=2, name="Factuur", document_count=309),
             dict(id=3, name="Unknown", document_count=2), dict(id=4, name="Nota", document_count=0), dict(id=5, name="Verslag", document_count=7)]
    merges, deletes, renames, review, members = dplan(types, vocab, 3)
    assert [(m["from_name"], m["into"]) for m in merges] == [("Factuur", 1)]
    assert [r["from"] for r in renames] == ["Invoice"] and renames[0]["to"] == "invoice"
    assert sorted(d["name"] for d in deletes) == ["Nota", "Unknown"] and [r["name"] for r in review] == ["Verslag"]

def test_target_name_mapped_elsewhere():
    import re
    from cleanup_doctypes import plan as dplan
    vocab = [("bank statement", [re.compile(r"bank statement|statement", re.I)]), ("statement", [re.compile(r"afrekening", re.I)])]
    types = [dict(id=1, name="Statement", document_count=10), dict(id=2, name="Afrekening", document_count=6), dict(id=3, name="Bank Statement", document_count=12)]
    merges, deletes, renames, review, members = dplan(types, vocab, 3)
    stm = next(m for m in merges if m["from_name"] == "Afrekening")
    assert stm["into"] is None and stm["into_name"] == "statement"      # not reusing 'Statement', which goes to bank statement
    assert [r["from"] for r in renames] == ["Bank Statement"]
