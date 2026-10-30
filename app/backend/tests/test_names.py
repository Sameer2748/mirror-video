"""The show's localisation sheet: two columns (source name, target name), csv or xlsx; old formats still read."""
import json
from pathlib import Path
from test_m1 import make_show
from dubstudio import names as nm


def test_two_column_csv_and_xlsx(tmp_path):
    a = tmp_path / "TOLR localisation.csv"
    a.write_text("source name,target name,notes\nEmily Kaiser,Emma Reali,lead\nJacob,Jacopo,\n,Missing,\nSame,same,\n")
    r = nm.read(a)
    assert r["format"] == "two-column" and r["names"] == [("Emily Kaiser", "Emma Reali"), ("Jacob", "Jacopo"), ("Same", "Same")]
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Source Name", "Target Name"]); ws.append(["Asher", "Demetrio"]); ws.append(["violet", "viola"])
    x = tmp_path / "names.xlsx"; wb.save(x)
    assert nm.read(x)["names"] == [("Asher", "Demetrio"), ("Violet", "Viola")]
    semi = tmp_path / "names_semicolon.csv"; semi.write_text("source name;target name\nAsher;Demetrio\n")
    assert nm.read(semi)["names"] == [("Asher", "Demetrio")]
    assert nm.to_csv([("Asher", "Demetrio")]) == "source name,target name\r\nAsher,Demetrio\r\n"


def test_old_formats_and_find(tmp_path):
    (tmp_path / "Master Adaptation Sheet.csv").write_text(
        "Type,ID,Canonical Name,Original Mention,Localized Mention\nCharacter,c1,a,Asher,Demetrio\nEntity,e1,s,the academy,l'accademia\n")
    (tmp_path / "TOLR - Localization Details.csv").write_text(
        "Type,ID,Original Name,Localized Name,First Name (Original),First Name (Localized)\nCharacter,c1,emily kaiser,emma reali,emily,emma\n")
    assert nm.read(tmp_path / "Master Adaptation Sheet.csv")["names"] == [("Asher", "Demetrio")]
    assert nm.read(tmp_path / "TOLR - Localization Details.csv")["names"] == [("Emily Kaiser", "Emma Reali"), ("Emily", "Emma")]
    assert len(nm.find(tmp_path)) == 2
    (tmp_path / "sub").mkdir(); (tmp_path / "sub" / "show_names.csv").write_text("source name,target name\nAsher,Demetrio\n")
    assert nm.find(tmp_path)[0].endswith("show_names.csv")                        # the two-column sheet wins
    big = [(f"N{k}", f"M{k}") for k in range(300)]
    assert nm.for_episode(big, ["only N7 here"]) == [("N7", "M7")] and len(nm.for_episode(big[:20], [])) == 20


def test_show_sheet_endpoints(env):
    c = env["client"]
    (env["show"] / "SHOW localisation.csv").write_text("source name,target name\nAsher,Demetrio\n")
    s = make_show(c, env["show"])
    assert s["profile"]["names_sheet"].endswith("SHOW localisation.csv")         # found when the show is made
    n = c.get(f"/api/shows/{s['id']}/names").json()
    assert n["count"] == 1 and n["format"] == "two-column" and n["names"] == [["Asher", "Demetrio"]]
    other = env["tmp"] / "x.csv"; other.write_text("a,b\n1,2\n")
    assert c.put(f"/api/shows/{s['id']}/names", json={"path": str(other)}).status_code == 400
    d = c.get(f"/api/shows/{s['id']}/names.csv")
    assert d.status_code == 200 and "source name,target name" in d.text and "Demetrio" in d.text
    assert c.put(f"/api/shows/{s['id']}/names", json={"path": None}).json()["chosen"] is False
