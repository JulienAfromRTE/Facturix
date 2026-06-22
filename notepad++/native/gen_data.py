# -*- coding: utf-8 -*-
"""Genere en16931_data.h (table C++) depuis facturix_en16931.py.
Garantit la parite exacte entre le plugin natif et le referentiel valide."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "FacturixBT"))
import facturix_en16931 as ref

def cstr(s):
    return '"' + s.replace("\\","\\\\").replace('"','\\"') + '"'

out = []
out.append("// GENERE AUTOMATIQUEMENT par gen_data.py - NE PAS EDITER A LA MAIN.")
out.append("// Source : notepad++/FacturixBT/facturix_en16931.py (valide vs CEF).")
out.append("#pragma once")
out.append("#include <vector>")
out.append("#include <string>")
out.append("struct BtRow { const char* code; const char* name; const char* xpath; };")
out.append("struct BgRow { const char* num; const char* xpath; };")
out.append("")
out.append("static const BtRow BT_ROWS[] = {")
for code, name, xpath in ref.all_bt_rows():
    out.append("    { %s, %s, %s }," % (cstr(code), cstr(name), cstr(xpath)))
out.append("};")
out.append("static const int BT_ROWS_N = sizeof(BT_ROWS)/sizeof(BT_ROWS[0]);")
out.append("")
out.append("static const BgRow BG_ROWS[] = {")
for num, xpath in ref.bg_containers():
    out.append("    { %s, %s }," % (cstr(num), cstr(xpath)))
out.append("};")
out.append("static const int BG_ROWS_N = sizeof(BG_ROWS)/sizeof(BG_ROWS[0]);")
out.append("")
data = "\n".join(out)
open(os.path.join(os.path.dirname(__file__), "en16931_data.h"), "w", encoding="utf-8").write(data)
print("BT rows:", len(ref.all_bt_rows()), " BG rows:", len(ref.bg_containers()))
print("en16931_data.h ecrit (%d lignes)" % (data.count(chr(10))+1))
