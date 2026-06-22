// ==========================================================================
//  Facturix - plugin natif Notepad++ (C++)
//
//  A l'ouverture d'un XML Factur-X / CII, annote chaque balise avec son
//  Business Term EN16931 (appellation officielle) en annotation EOL a droite,
//  et encadre chaque groupe BG par son numero (ouverture / fermeture).
//
//  Portage fidele de la version PythonScript validee :
//   - parseur XML maison avec suivi des numeros de ligne (comme expat) ;
//   - mini-evaluateur XPath (sous-ensemble EN16931) ;
//   - table EN16931 GENEREE depuis facturix_en16931.py (en16931_data.h).
//
//  Sans dependance externe (pas de Python, pas de SQLite, pas de lxml).
//  En-tetes officiels requis : PluginInterface.h, Scintilla.h,
//  Notepad_plus_msgs.h (recuperes par le script de build).
// ==========================================================================

#include <windows.h>
#include <commctrl.h>
#include <string>
#include <vector>
#include <map>
#include <set>
#include <cstring>
#include <cstdio>
#include <cstdlib>
#include <cctype>

#include "PluginInterface.h"
#include "Scintilla.h"
#include "Notepad_plus_msgs.h"
#include "Docking.h"

#include "en16931_data.h"

// --------------------------------------------------------------------------
//  Etat global du plugin
// --------------------------------------------------------------------------
static NppData s_npp;
static FuncItem s_funcItems[2];
static bool s_enabled = false;
static HINSTANCE s_hInst = NULL;

static const int BT_STYLE = 240;
static const int BG_STYLE = 241;

// --------------------------------------------------------------------------
//  Helpers Scintilla / Notepad++
// --------------------------------------------------------------------------
static HWND currentScintilla()
{
    int which = -1;
    ::SendMessage(s_npp._nppHandle, NPPM_GETCURRENTSCINTILLA, 0, (LPARAM)&which);
    if (which == -1) return NULL;
    return (which == 0) ? s_npp._scintillaMainHandle : s_npp._scintillaSecondHandle;
}

static LRESULT sci(HWND h, UINT msg, WPARAM w = 0, LPARAM l = 0)
{
    return ::SendMessage(h, msg, w, l);
}

static std::string getDocText(HWND h)
{
    LRESULT len = sci(h, SCI_GETLENGTH);
    if (len <= 0) return std::string();
    std::vector<char> buf((size_t)len + 1, 0);
    sci(h, SCI_GETTEXT, (WPARAM)(len + 1), (LPARAM)buf.data());
    return std::string(buf.data(), (size_t)len);
}

// ==========================================================================
//  1. Parseur XML avec numeros de ligne (equivalent du parseur expat Python)
// ==========================================================================
struct Node {
    std::string tag;                              // nom qualifie, ex "ram:ID"
    std::map<std::string, std::string> attrs;     // attributs bruts
    std::vector<Node*> children;
    std::string text;
    int line;       // 1-based, balise ouvrante
    int end_line;   // 1-based, balise fermante
    Node* parent;
    Node() : line(0), end_line(0), parent(NULL) {}
};

static std::vector<Node*> s_pool;   // pour liberation

static void freePool()
{
    for (size_t i = 0; i < s_pool.size(); ++i) delete s_pool[i];
    s_pool.clear();
}

static Node* newNode()
{
    Node* n = new Node();
    s_pool.push_back(n);
    return n;
}

// parse les attributs d'une balise ouvrante (entre le nom et '>' ou '/')
static void parseAttrs(const std::string& s, size_t i, size_t end, Node* node)
{
    while (i < end) {
        while (i < end && (unsigned char)s[i] <= ' ') ++i;
        if (i >= end) break;
        size_t ns = i;
        while (i < end && s[i] != '=' && (unsigned char)s[i] > ' ') ++i;
        std::string name = s.substr(ns, i - ns);
        while (i < end && (unsigned char)s[i] <= ' ') ++i;
        if (i < end && s[i] == '=') {
            ++i;
            while (i < end && (unsigned char)s[i] <= ' ') ++i;
            if (i < end && (s[i] == '"' || s[i] == '\'')) {
                char q = s[i++];
                size_t vs = i;
                while (i < end && s[i] != q) ++i;
                std::string val = s.substr(vs, i - vs);
                if (i < end) ++i;
                if (!name.empty()) node->attrs[name] = val;
            }
        }
    }
}

// Construit l'arbre. Retourne la racine (ou NULL).
static Node* parseXml(const std::string& s)
{
    freePool();
    Node* root = NULL;
    std::vector<Node*> stack;
    int line = 1;
    size_t i = 0, n = s.size();

    while (i < n) {
        char c = s[i];
        if (c == '\n') { ++line; ++i; continue; }
        if (c == '<') {
            // commentaires / declarations / CDATA
            if (s.compare(i, 4, "<!--") == 0) {
                size_t e = s.find("-->", i + 4);
                size_t j = (e == std::string::npos) ? n : e + 3;
                for (size_t k = i; k < j; ++k) if (s[k] == '\n') ++line;
                i = j; continue;
            }
            if (s.compare(i, 9, "<![CDATA[") == 0) {
                size_t e = s.find("]]>", i + 9);
                size_t j = (e == std::string::npos) ? n : e + 3;
                if (!stack.empty())
                    stack.back()->text += s.substr(i + 9,
                        (e == std::string::npos ? n : e) - (i + 9));
                for (size_t k = i; k < j; ++k) if (s[k] == '\n') ++line;
                i = j; continue;
            }
            if (s.compare(i, 2, "<?") == 0) {
                size_t e = s.find("?>", i + 2);
                size_t j = (e == std::string::npos) ? n : e + 2;
                for (size_t k = i; k < j; ++k) if (s[k] == '\n') ++line;
                i = j; continue;
            }
            if (s.compare(i, 2, "<!") == 0) {  // DOCTYPE etc.
                size_t e = s.find('>', i + 2);
                size_t j = (e == std::string::npos) ? n : e + 1;
                for (size_t k = i; k < j; ++k) if (s[k] == '\n') ++line;
                i = j; continue;
            }
            // fin de balise </name>
            if (s.compare(i, 2, "</") == 0) {
                size_t e = s.find('>', i + 2);
                size_t j = (e == std::string::npos) ? n : e + 1;
                if (!stack.empty()) {
                    stack.back()->end_line = line;
                    stack.pop_back();
                }
                i = j; continue;
            }
            // balise ouvrante <name ...> ou auto-fermante <name .../>
            size_t e = s.find('>', i + 1);
            if (e == std::string::npos) break;
            int startLine = line;
            for (size_t k = i; k <= e; ++k) if (s[k] == '\n') ++line; // lignes du tag
            bool selfClose = (e > i && s[e - 1] == '/');
            size_t nameStart = i + 1;
            size_t p = nameStart;
            size_t tagEnd = selfClose ? e - 1 : e;
            while (p < tagEnd && (unsigned char)s[p] > ' ' && s[p] != '/') ++p;
            std::string name = s.substr(nameStart, p - nameStart);

            Node* node = newNode();
            node->tag = name;
            node->line = startLine;
            node->end_line = startLine;
            node->parent = stack.empty() ? NULL : stack.back();
            parseAttrs(s, p, tagEnd, node);

            if (node->parent) node->parent->children.push_back(node);
            else if (!root) root = node;

            if (!selfClose) stack.push_back(node);
            i = e + 1; continue;
        }
        // texte
        if (!stack.empty()) stack.back()->text += c;
        ++i;
    }
    return root;
}

// ==========================================================================
//  2. Mini-evaluateur XPath (portage 1:1 de facturix_core.py)
// ==========================================================================
typedef std::pair<std::string, std::string> Step;  // (axis, stepStr)

static std::string trim(const std::string& s)
{
    size_t a = 0, b = s.size();
    while (a < b && (unsigned char)s[a] <= ' ') ++a;
    while (b > a && (unsigned char)s[b - 1] <= ' ') --b;
    return s.substr(a, b - a);
}

static std::vector<Step> splitSteps(const std::string& path)
{
    std::vector<Step> steps;
    std::string buf, pendingAxis;
    int depth = 0;
    size_t i = 0, n = path.size();
    while (i < n) {
        char ch = path[i];
        if (ch == '[') { ++depth; buf += ch; }
        else if (ch == ']') { --depth; buf += ch; }
        else if (ch == '/' && depth == 0) {
            if (!buf.empty()) {
                steps.push_back(Step(pendingAxis.empty() ? "/" : pendingAxis, buf));
                buf.clear();
            }
            if (i + 1 < n && path[i + 1] == '/') { pendingAxis = "//"; ++i; }
            else pendingAxis = "/";
        }
        else buf += ch;
        ++i;
    }
    if (!buf.empty())
        steps.push_back(Step(pendingAxis.empty() ? "/" : pendingAxis, buf));
    return steps;
}

static void parseStep(const std::string& step, std::string& name,
                      std::vector<std::string>& preds)
{
    name = step; preds.clear();
    size_t br = step.find('[');
    if (br == std::string::npos) return;
    name = step.substr(0, br);
    std::string rest = step.substr(br);
    int depth = 0; std::string cur;
    for (size_t k = 0; k < rest.size(); ++k) {
        char ch = rest[k];
        if (ch == '[') { ++depth; if (depth == 1) { cur.clear(); continue; } }
        if (ch == ']') { --depth; if (depth == 0) { preds.push_back(cur); cur.clear(); continue; } }
        cur += ch;
    }
}

static std::vector<Node*> childrenNamed(Node* node, const std::string& name)
{
    std::vector<Node*> r;
    for (size_t k = 0; k < node->children.size(); ++k)
        if (node->children[k]->tag == name) r.push_back(node->children[k]);
    return r;
}

static bool evalPred(Node* node, const std::string& predRaw);

static std::vector<std::string> resolveRel(Node* node, const std::vector<Step>& steps)
{
    std::vector<std::string> out;
    if (steps.empty()) { out.push_back(node->text); return out; }
    const std::string& axis = steps[0].first;
    const std::string& head = steps[0].second;
    if (!head.empty() && head[0] == '@') {
        std::string a = head.substr(1);
        std::map<std::string, std::string>::iterator it = node->attrs.find(a);
        if (it != node->attrs.end()) out.push_back(it->second);
        return out;
    }
    std::string name; std::vector<std::string> preds;
    parseStep(head, name, preds);

    std::vector<Node*> cands;
    if (axis == "//") {
        std::vector<Node*> st(node->children);
        while (!st.empty()) {
            Node* x = st.back(); st.pop_back();
            if (x->tag == name) cands.push_back(x);
            for (size_t k = 0; k < x->children.size(); ++k) st.push_back(x->children[k]);
        }
    } else {
        cands = childrenNamed(node, name);
    }
    std::vector<Node*> matched;
    for (size_t k = 0; k < cands.size(); ++k) {
        bool ok = true;
        for (size_t p = 0; p < preds.size(); ++p)
            if (!evalPred(cands[k], preds[p])) { ok = false; break; }
        if (ok) matched.push_back(cands[k]);
    }
    std::vector<Step> rest(steps.begin() + 1, steps.end());
    for (size_t k = 0; k < matched.size(); ++k) {
        Node* m = matched[k];
        if (!rest.empty() && !rest[0].second.empty() && rest[0].second[0] == '@') {
            std::string a = rest[0].second.substr(1);
            std::map<std::string, std::string>::iterator it = m->attrs.find(a);
            if (it != m->attrs.end()) out.push_back(it->second);
        } else if (!rest.empty()) {
            std::vector<std::string> sub = resolveRel(m, rest);
            out.insert(out.end(), sub.begin(), sub.end());
        } else {
            out.push_back(m->text);
        }
    }
    return out;
}

static bool evalPred(Node* node, const std::string& predRaw)
{
    std::string pred = trim(predRaw);
    size_t eq = pred.find('=');
    if (eq != std::string::npos) {
        std::string left = trim(pred.substr(0, eq));
        std::string right = trim(pred.substr(eq + 1));
        if (right.size() >= 2 && (right[0] == '\'' || right[0] == '"'))
            right = right.substr(1, right.size() - 2);
        std::vector<Step> steps = splitSteps(left);
        std::vector<std::string> vals = resolveRel(node, steps);
        for (size_t k = 0; k < vals.size(); ++k)
            if (trim(vals[k]) == right) return true;
        return false;
    }
    std::vector<Step> steps = splitSteps(trim(pred));
    return !resolveRel(node, steps).empty();
}

static std::vector<Node*> evalXpath(Node* root, const std::string& xpath)
{
    std::vector<Node*> empty;
    if (!root || xpath.empty()) return empty;
    std::string xp = trim(xpath);
    const std::string txt = "/text()";
    if (xp.size() >= txt.size() && xp.compare(xp.size() - txt.size(), txt.size(), txt) == 0)
        xp = xp.substr(0, xp.size() - txt.size());
    bool absolute = (!xp.empty() && xp[0] == '/');

    std::vector<Step> steps = splitSteps(xp);
    if (!steps.empty() && !steps.back().second.empty() && steps.back().second[0] == '@')
        steps.pop_back();
    if (steps.empty()) return empty;

    std::string name0; std::vector<std::string> preds0;
    parseStep(steps[0].second, name0, preds0);
    std::string axis0 = steps[0].first;

    std::vector<Node*> current;
    size_t startIdx;
    if (axis0 == "//") {
        std::vector<Node*> st; st.push_back(root);
        while (!st.empty()) {
            Node* nd = st.back(); st.pop_back();
            bool ok = (nd->tag == name0);
            for (size_t p = 0; ok && p < preds0.size(); ++p)
                if (!evalPred(nd, preds0[p])) ok = false;
            if (ok) current.push_back(nd);
            for (size_t k = 0; k < nd->children.size(); ++k) st.push_back(nd->children[k]);
        }
        startIdx = 1;
    } else if (absolute) {
        bool ok = (root->tag == name0);
        for (size_t p = 0; ok && p < preds0.size(); ++p)
            if (!evalPred(root, preds0[p])) ok = false;
        if (ok) current.push_back(root);
        startIdx = 1;
    } else {
        current.push_back(root);
        startIdx = 0;
    }

    for (size_t si = startIdx; si < steps.size(); ++si) {
        if (current.empty()) break;
        std::string axis = steps[si].first;
        std::string name; std::vector<std::string> preds;
        parseStep(steps[si].second, name, preds);
        std::vector<Node*> next;
        for (size_t c = 0; c < current.size(); ++c) {
            Node* nd = current[c];
            if (axis == "//") {
                std::vector<Node*> st(nd->children);
                while (!st.empty()) {
                    Node* x = st.back(); st.pop_back();
                    bool ok = (x->tag == name);
                    for (size_t p = 0; ok && p < preds.size(); ++p)
                        if (!evalPred(x, preds[p])) ok = false;
                    if (ok) next.push_back(x);
                    for (size_t k = 0; k < x->children.size(); ++k) st.push_back(x->children[k]);
                }
            } else {
                std::vector<Node*> ch = childrenNamed(nd, name);
                for (size_t k = 0; k < ch.size(); ++k) {
                    bool ok = true;
                    for (size_t p = 0; ok && p < preds.size(); ++p)
                        if (!evalPred(ch[k], preds[p])) ok = false;
                    if (ok) next.push_back(ch[k]);
                }
            }
        }
        current.swap(next);
    }
    return current;
}

// ==========================================================================
//  3. Indexation BT + marqueurs BG
// ==========================================================================
struct BtHit { std::string code; std::string name; };

static std::map<int, std::vector<BtHit> > s_btIndex;
static std::map<int, std::vector<std::string> > s_openM;
static std::map<int, std::vector<std::string> > s_closeM;

static void buildIndex(Node* root)
{
    s_btIndex.clear(); s_openM.clear(); s_closeM.clear();
    std::set<std::pair<int, std::string> > seen;
    for (int r = 0; r < BT_ROWS_N; ++r) {
        std::string code = BT_ROWS[r].code;
        std::string name = BT_ROWS[r].name;
        std::vector<Node*> nodes = evalXpath(root, BT_ROWS[r].xpath);
        for (size_t k = 0; k < nodes.size(); ++k) {
            std::pair<int, std::string> key(nodes[k]->line, code);
            if (seen.count(key)) continue;
            seen.insert(key);
            BtHit h; h.code = code; h.name = name;
            s_btIndex[nodes[k]->line].push_back(h);
        }
    }
    for (int r = 0; r < BG_ROWS_N; ++r) {
        std::string num = BG_ROWS[r].num;
        std::vector<Node*> nodes = evalXpath(root, BG_ROWS[r].xpath);
        for (size_t k = 0; k < nodes.size(); ++k) {
            s_openM[nodes[k]->line].push_back(num);
            s_closeM[nodes[k]->end_line].push_back(num);
        }
    }
}

// ==========================================================================
//  4. Rendu : annotations EOL
// ==========================================================================
static const char* EOL_PREFIX = "  ";
static const char* SEP = "   |   ";

static std::string fmtLine(int line1, bool& isBgOnly)
{
    std::vector<std::string>& opens = s_openM[line1];
    std::vector<BtHit>& bts = s_btIndex[line1];
    std::vector<std::string>& closes = s_closeM[line1];
    std::vector<std::string> parts;
    for (size_t k = 0; k < opens.size(); ++k) parts.push_back(std::string("\xE2\x94\x8C\xE2\x94\x80 ") + opens[k]); // "┌─ "
    for (size_t k = 0; k < bts.size(); ++k) parts.push_back(bts[k].code + " \xE2\x80\x94 " + bts[k].name); // " — "
    for (size_t k = 0; k < closes.size(); ++k) parts.push_back(std::string("\xE2\x94\x94\xE2\x94\x80 ") + closes[k]); // "└─ "
    isBgOnly = (!closes.empty() || !opens.empty()) && bts.empty();
    if (parts.empty()) return std::string();

    std::string prefix = EOL_PREFIX;
    // ligne de fermeture pure : un cran a gauche pour compenser le "/"
    if (!closes.empty() && opens.empty() && bts.empty() && !prefix.empty())
        prefix = prefix.substr(0, prefix.size() - 1);

    std::string out = prefix;
    for (size_t k = 0; k < parts.size(); ++k) {
        if (k) out += SEP;
        out += parts[k];
    }
    return out;
}

static void clearAnnotations(HWND h)
{
    sci(h, SCI_EOLANNOTATIONCLEARALL);
}

static void render(HWND h)
{
    if (!h) return;
    clearAnnotations(h);
    s_btIndex.clear(); s_openM.clear(); s_closeM.clear();
    if (!s_enabled) return;

    std::string text = getDocText(h);
    if (text.find("CrossIndustryInvoice") == std::string::npos) return;

    Node* root = parseXml(text);
    if (!root) return;
    buildIndex(root);

    // styles
    COLORREF btCol = (COLORREF)(90 | (110 << 8) | (170 << 16));
    COLORREF bgCol = (COLORREF)(150 | (110 << 8) | (60 << 16));
    sci(h, SCI_STYLESETFORE, BT_STYLE, (LPARAM)btCol);
    sci(h, SCI_STYLESETFORE, BG_STYLE, (LPARAM)bgCol);
    sci(h, SCI_STYLESETITALIC, BT_STYLE, 1);
    sci(h, SCI_EOLANNOTATIONSETVISIBLE, EOLANNOTATION_STANDARD);

    std::set<int> lines;
    for (std::map<int, std::vector<BtHit> >::iterator it = s_btIndex.begin(); it != s_btIndex.end(); ++it) lines.insert(it->first);
    for (std::map<int, std::vector<std::string> >::iterator it = s_openM.begin(); it != s_openM.end(); ++it) lines.insert(it->first);
    for (std::map<int, std::vector<std::string> >::iterator it = s_closeM.begin(); it != s_closeM.end(); ++it) lines.insert(it->first);

    for (std::set<int>::iterator it = lines.begin(); it != lines.end(); ++it) {
        int line1 = *it;
        bool bgOnly = false;
        std::string txt = fmtLine(line1, bgOnly);
        if (txt.empty()) continue;
        int style = bgOnly ? BG_STYLE : BT_STYLE;
        sci(h, SCI_EOLANNOTATIONSETTEXT, (WPARAM)(line1 - 1), (LPARAM)txt.c_str());
        sci(h, SCI_EOLANNOTATIONSETSTYLE, (WPARAM)(line1 - 1), (LPARAM)style);
    }
}

// ==========================================================================
//  5. Commande de menu : bascule ON / OFF
// ==========================================================================
static void toggleFacturix()
{
    s_enabled = !s_enabled;
    HWND h = currentScintilla();
    if (s_enabled) render(h);
    else if (h) clearAnnotations(h);
}

// ==========================================================================
//  6. Validation Schematron officielle (Saxon embarque) + panneau docke bas
// ==========================================================================
static const char* LINE_XPATH =
    "/rsm:CrossIndustryInvoice/rsm:SupplyChainTradeTransaction/ram:IncludedSupplyChainTradeLineItem";
static const char* PROFILE_XPATH =
    "/rsm:CrossIndustryInvoice/rsm:ExchangedDocumentContext/ram:GuidelineSpecifiedDocumentContextParameter/ram:ID";

struct Finding {
    std::string sev;       // "ERREUR" | "WARN" | "INFO"
    std::string rule;      // id de la regle (BR-...)
    std::string location;  // XPath SVRL
    std::string message;
    std::string label;     // libelle du jeu de regles
    int line;              // ligne 1-based (0 = inconnue)
};

static HWND s_panel = NULL;
static HWND s_list = NULL;
static std::vector<Finding> s_findings;

// --- conversions UTF-8 <-> UTF-16 -----------------------------------------
static std::wstring widen(const std::string& s)
{
    if (s.empty()) return L"";
    int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), NULL, 0);
    std::wstring w(n, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &w[0], n);
    return w;
}
static std::string narrow(const std::wstring& w)
{
    if (w.empty()) return "";
    int n = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), (int)w.size(), NULL, 0, NULL, NULL);
    std::string s(n, 0);
    WideCharToMultiByte(CP_UTF8, 0, w.c_str(), (int)w.size(), &s[0], n, NULL, NULL);
    return s;
}

// --- chemins du moteur embarque -------------------------------------------
static std::wstring pluginDir()
{
    wchar_t buf[MAX_PATH]; GetModuleFileNameW(s_hInst, buf, MAX_PATH);
    std::wstring p(buf); size_t s = p.find_last_of(L"\\/");
    return (s == std::wstring::npos) ? L"." : p.substr(0, s);
}
static std::wstring engineDir() { return pluginDir() + L"\\engine"; }

// --- profil (BT-24) : portage de classify_profile / rulesets_for_profile ---
static std::string toLower(std::string s)
{
    for (size_t i = 0; i < s.size(); ++i) s[i] = (char)tolower((unsigned char)s[i]);
    return s;
}
static std::string classifyProfile(const std::string& uri)
{
    std::string s = toLower(uri);
    if (s.empty()) return "unknown";
    if (s.find("extended-ctc-fr") != std::string::npos ||
        (s.find("cpro.gouv") != std::string::npos && s.find("extended") != std::string::npos))
        return "extended-ctc-fr";
    if (s.find("extended") != std::string::npos) return "extended";
    if (s.find("basicwl") != std::string::npos || s.find("basic wl") != std::string::npos ||
        s.find("basic-wl") != std::string::npos) return "basicwl";
    if (s.find("basic") != std::string::npos) return "basic";
    if (s.find("minimum") != std::string::npos) return "minimum";
    if (s.find("en16931") != std::string::npos || s.find("comfort") != std::string::npos)
        return "en16931";
    return "unknown";
}
static std::vector<std::string> rulesetsForProfile(const std::string& cls)
{
    std::vector<std::string> r;
    if (cls == "minimum" || cls == "basicwl") return r;          // aucun
    if (cls == "extended-ctc-fr") { r.push_back("extended-ctc-fr"); r.push_back("br-fr-flux2"); return r; }
    r.push_back("en16931"); return r;
}
static std::wstring xsltFile(const std::string& key)
{
    if (key == "extended-ctc-fr") return L"EXTENDED-CTC-FR-CII.xslt";
    if (key == "br-fr-flux2")     return L"BR-FR-Flux2-CII.xslt";
    return L"EN16931-CII-validation.xslt";
}
static std::string rulesetLabel(const std::string& key)
{
    if (key == "extended-ctc-fr") return "EXTENDED-CTC-FR (CII)";
    if (key == "br-fr-flux2")     return "France CTC - BR-FR Flux 2";
    return "EN16931-CII v1.3.16";
}

// --- lancer un process cache et attendre la fin ----------------------------
static bool runHidden(const std::wstring& cmd)
{
    STARTUPINFOW si; ZeroMemory(&si, sizeof(si)); si.cb = sizeof(si);
    si.dwFlags = STARTF_USESHOWWINDOW; si.wShowWindow = SW_HIDE;
    PROCESS_INFORMATION pi; ZeroMemory(&pi, sizeof(pi));
    std::vector<wchar_t> cb(cmd.begin(), cmd.end()); cb.push_back(0);
    BOOL ok = CreateProcessW(NULL, cb.data(), NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, NULL, &si, &pi);
    if (!ok) return false;
    WaitForSingleObject(pi.hProcess, INFINITE);
    CloseHandle(pi.hProcess); CloseHandle(pi.hThread);
    return true;
}

static std::string readFileAll(const std::wstring& path)
{
    HANDLE h = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, NULL,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return "";
    std::string out; char buf[8192]; DWORD n = 0;
    while (ReadFile(h, buf, sizeof(buf), &n, NULL) && n > 0) out.append(buf, n);
    CloseHandle(h);
    return out;
}

// --- SVRL : extraction des failed-assert (scanner cible, sans toucher au parseur) ---
static std::string svrlAttr(const std::string& tag, const std::string& name)
{
    size_t p = tag.find(name + "=\"");
    if (p == std::string::npos) { p = tag.find(name + "='"); if (p == std::string::npos) return ""; }
    p += name.size() + 2; char q = tag[p - 1];
    size_t e = tag.find(q, p);
    return (e == std::string::npos) ? "" : tag.substr(p, e - p);
}
static std::string stripTags(const std::string& s)
{
    std::string out; bool in = false;
    for (size_t i = 0; i < s.size(); ++i) {
        char c = s[i];
        if (c == '<') in = true;
        else if (c == '>') in = false;
        else if (!in) out += c;
    }
    // compacter les blancs
    std::string r; bool sp = false;
    for (size_t i = 0; i < out.size(); ++i) {
        char c = out[i];
        if (c == '\n' || c == '\r' || c == '\t' || c == ' ') { sp = true; }
        else { if (sp && !r.empty()) r += ' '; sp = false; r += c; }
    }
    return r;
}
static int resolveLine(const std::string& loc, const std::vector<int>& liStarts)
{
    size_t p = loc.find("IncludedSupplyChainTradeLineItem");
    if (p == std::string::npos) return 0;
    size_t slash = loc.find('/', p);
    size_t lim = (slash == std::string::npos) ? loc.size() : slash;
    size_t br = loc.find('[', p);
    while (br != std::string::npos && br < lim) {
        size_t q = br + 1; std::string num;
        while (q < loc.size() && isdigit((unsigned char)loc[q])) { num += loc[q]; ++q; }
        if (!num.empty()) {
            int idx = atoi(num.c_str());
            if (idx >= 1 && idx <= (int)liStarts.size()) return liStarts[idx - 1];
        }
        br = loc.find('[', q);
    }
    return 0;
}
static void parseSvrl(const std::string& svrl, const std::string& label,
                      const std::vector<int>& liStarts, std::vector<Finding>& out)
{
    size_t pos = 0;
    while ((pos = svrl.find("failed-assert", pos)) != std::string::npos) {
        size_t tagStart = svrl.rfind('<', pos);
        size_t tagEnd = svrl.find('>', pos);
        if (tagStart == std::string::npos || tagEnd == std::string::npos) break;
        std::string opentag = svrl.substr(tagStart, tagEnd - tagStart + 1);
        std::string flag = svrlAttr(opentag, "flag");
        std::string loc = svrlAttr(opentag, "location");
        std::string id = svrlAttr(opentag, "id");
        size_t close = svrl.find("failed-assert>", tagEnd);
        std::string inner = (close != std::string::npos)
            ? svrl.substr(tagEnd + 1, close - (tagEnd + 1)) : "";
        Finding f;
        f.sev = (flag == "fatal") ? "ERREUR" : "WARN";
        f.rule = id; f.location = loc; f.message = stripTags(inner);
        f.label = label; f.line = resolveLine(loc, liStarts);
        out.push_back(f);
        pos = (close != std::string::npos) ? close + 1 : tagEnd + 1;
    }
}

// --- fichier temporaire ----------------------------------------------------
static std::wstring writeTempXml(const std::string& text)
{
    wchar_t tmp[MAX_PATH]; GetTempPathW(MAX_PATH, tmp);
    std::wstring path = std::wstring(tmp) + L"facturix_npp_input.xml";
    HANDLE h = CreateFileW(path.c_str(), GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE) return L"";
    DWORD w = 0; WriteFile(h, text.data(), (DWORD)text.size(), &w, NULL);
    CloseHandle(h);
    return path;
}

// --- panneau docke (ListView) ---------------------------------------------
static void jumpToLine(int line1)
{
    if (line1 <= 0) return;
    HWND sc = currentScintilla();
    if (!sc) return;
    sci(sc, SCI_GOTOLINE, (WPARAM)(line1 - 1));
    sci(sc, SCI_SCROLLCARET);
    SetFocus(sc);
}

static LRESULT CALLBACK PanelProc(HWND hWnd, UINT msg, WPARAM w, LPARAM l)
{
    if (msg == WM_SIZE) {
        if (s_list) { RECT rc; GetClientRect(hWnd, &rc); MoveWindow(s_list, 0, 0, rc.right, rc.bottom, TRUE); }
        return 0;
    }
    if (msg == WM_NOTIFY) {
        NMHDR* nh = (NMHDR*)l;
        if (nh->code == NM_DBLCLK) {
            NMITEMACTIVATE* ia = (NMITEMACTIVATE*)l;
            int sel = ia->iItem;
            if (sel >= 0 && sel < (int)s_findings.size()) jumpToLine(s_findings[sel].line);
            return 0;
        }
    }
    return DefWindowProc(hWnd, msg, w, l);
}

static void ensurePanel()
{
    if (s_panel) return;
    INITCOMMONCONTROLSEX icc; icc.dwSize = sizeof(icc); icc.dwICC = ICC_LISTVIEW_CLASSES;
    InitCommonControlsEx(&icc);

    WNDCLASSW wc; ZeroMemory(&wc, sizeof(wc));
    wc.lpfnWndProc = PanelProc; wc.hInstance = s_hInst;
    wc.lpszClassName = L"FacturixPanelClass";
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    RegisterClassW(&wc);

    s_panel = CreateWindowExW(0, L"FacturixPanelClass", L"",
        WS_CHILD | WS_CLIPCHILDREN, 0, 0, 600, 200,
        s_npp._nppHandle, NULL, s_hInst, NULL);
    s_list = CreateWindowExW(0, WC_LISTVIEW, L"",
        WS_CHILD | WS_VISIBLE | LVS_REPORT | LVS_SINGLESEL,
        0, 0, 600, 200, s_panel, NULL, s_hInst, NULL);
    ListView_SetExtendedListViewStyle(s_list, LVS_EX_FULLROWSELECT | LVS_EX_GRIDLINES);

    const wchar_t* titles[] = { L"Sev.", L"Regle", L"Ligne", L"Message", L"Jeu de regles" };
    int widths[] = { 60, 120, 55, 760, 200 };
    LVCOLUMNW col; ZeroMemory(&col, sizeof(col)); col.mask = LVCF_TEXT | LVCF_WIDTH;
    for (int i = 0; i < 5; ++i) { col.pszText = (LPWSTR)titles[i]; col.cx = widths[i]; ListView_InsertColumn(s_list, i, &col); }

    static tTbData data;  // statique : NPP conserve les pointeurs (pszName...)
    ZeroMemory(&data, sizeof(data));
    data.hClient = s_panel;
    data.pszName = L"Facturix — Validation Schematron";
    data.dlgID = 1;
    data.uMask = DWS_DF_CONT_BOTTOM;
    data.pszModuleName = L"Facturix.dll";
    ::SendMessage(s_npp._nppHandle, NPPM_DMMREGASDCKDLG, 0, (LPARAM)&data);
}

static void setItem(int row, int col, const std::string& text)
{
    std::wstring w = widen(text);
    ListView_SetItemText(s_list, row, col, (LPWSTR)w.c_str());
}
static void populatePanel()
{
    ensurePanel();
    ListView_DeleteAllItems(s_list);
    for (size_t i = 0; i < s_findings.size(); ++i) {
        LVITEMW it; ZeroMemory(&it, sizeof(it));
        it.mask = LVIF_TEXT | LVIF_PARAM; it.iItem = (int)i; it.iSubItem = 0;
        std::wstring sev = widen(s_findings[i].sev);
        it.pszText = (LPWSTR)sev.c_str(); it.lParam = (LPARAM)i;
        int row = ListView_InsertItem(s_list, &it);
        setItem(row, 1, s_findings[i].rule);
        setItem(row, 2, s_findings[i].line > 0 ? std::to_string(s_findings[i].line) : std::string("-"));
        setItem(row, 3, s_findings[i].message);
        setItem(row, 4, s_findings[i].label);
    }
    ::SendMessage(s_npp._nppHandle, NPPM_DMMSHOW, 0, (LPARAM)s_panel);
}

// --- commande : valider le document courant --------------------------------
static void validateCmd()
{
    s_findings.clear();
    HWND h = currentScintilla();
    if (!h) return;
    std::string text = getDocText(h);
    if (text.find("CrossIndustryInvoice") == std::string::npos) {
        Finding f; f.sev = "INFO"; f.rule = "-"; f.line = 0; f.label = "-";
        f.message = "Le document courant n'est pas une facture CII / Factur-X.";
        s_findings.push_back(f); populatePanel(); return;
    }

    Node* root = parseXml(text);
    std::string profUri;
    { std::vector<Node*> n = evalXpath(root, PROFILE_XPATH); if (!n.empty()) profUri = n[0]->text; }
    std::string cls = classifyProfile(profUri);
    std::vector<std::string> keys = rulesetsForProfile(cls);

    std::vector<int> liStarts;
    { std::vector<Node*> ls = evalXpath(root, LINE_XPATH);
      for (size_t k = 0; k < ls.size(); ++k) liStarts.push_back(ls[k]->line); }

    if (keys.empty()) {
        Finding f; f.sev = "INFO"; f.rule = "-"; f.line = 0; f.label = cls;
        f.message = "Profil " + cls + " : non soumis au schematron EN16931 (pas de faux positifs).";
        s_findings.push_back(f); populatePanel(); return;
    }

    std::wstring xmlPath = writeTempXml(text);
    std::wstring eng = engineDir();
    wchar_t tmpdir[MAX_PATH]; GetTempPathW(MAX_PATH, tmpdir);
    std::wstring outPath = std::wstring(tmpdir) + L"facturix_npp_out.svrl";
    int nbErr = 0, nbWarn = 0;
    std::vector<Finding> tmp;
    for (size_t k = 0; k < keys.size(); ++k) {
        DeleteFileW(outPath.c_str());
        std::wstring cmd = L"\"" + eng + L"\\jre\\bin\\java.exe\" -jar \"" + eng +
            L"\\saxon-he.jar\" -s:\"" + xmlPath + L"\" -xsl:\"" + eng + L"\\xslt\\" +
            xsltFile(keys[k]) + L"\" -o:\"" + outPath + L"\"";
        std::string svrl;
        if (runHidden(cmd)) svrl = readFileAll(outPath);
        if (svrl.find("schematron-output") == std::string::npos) {
            Finding f; f.sev = "ERREUR"; f.rule = "-"; f.line = 0; f.label = rulesetLabel(keys[k]);
            f.message = "Echec du moteur Saxon (verifier engine\\jre et engine\\saxon-he.jar).";
            tmp.push_back(f); continue;
        }
        parseSvrl(svrl, rulesetLabel(keys[k]), liStarts, tmp);
    }
    for (size_t i = 0; i < tmp.size(); ++i) { if (tmp[i].sev == "ERREUR") ++nbErr; else if (tmp[i].sev == "WARN") ++nbWarn; }

    Finding head; head.sev = "INFO"; head.rule = cls.empty() ? "-" : cls; head.line = 0;
    head.label = profUri.empty() ? "(profil non declare)" : profUri;
    char buf[128]; sprintf(buf, "%d erreur(s), %d avertissement(s)", nbErr, nbWarn);
    head.message = buf;
    s_findings.push_back(head);
    for (size_t i = 0; i < tmp.size(); ++i) s_findings.push_back(tmp[i]);
    populatePanel();
}

// ==========================================================================
//  7. Interface plugin Notepad++
// ==========================================================================
BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH) {
        s_hInst = hModule;
        lstrcpy(s_funcItems[0]._itemName, TEXT("Activer / desactiver les annotations BT/BG"));
        s_funcItems[0]._pFunc = toggleFacturix;
        s_funcItems[0]._init2Check = false;
        s_funcItems[0]._pShKey = NULL;
        s_funcItems[0]._cmdID = 0;
        lstrcpy(s_funcItems[1]._itemName, TEXT("Valider (Schematron officiel)"));
        s_funcItems[1]._pFunc = validateCmd;
        s_funcItems[1]._init2Check = false;
        s_funcItems[1]._pShKey = NULL;
        s_funcItems[1]._cmdID = 0;
    }
    return TRUE;
}

extern "C" __declspec(dllexport) void setInfo(NppData notpadPlusData)
{
    s_npp = notpadPlusData;
}

extern "C" __declspec(dllexport) const TCHAR* getName()
{
    return TEXT("Facturix");
}

extern "C" __declspec(dllexport) FuncItem* getFuncsArray(int* nbF)
{
    *nbF = 2;
    return s_funcItems;
}

extern "C" __declspec(dllexport) void beNotified(SCNotification* notify)
{
    if (!notify) return;
    unsigned int code = notify->nmhdr.code;
    if (!s_enabled) return;
    if (code == NPPN_BUFFERACTIVATED || code == NPPN_FILESAVED ||
        code == NPPN_FILEOPENED) {
        render(currentScintilla());
    }
}

extern "C" __declspec(dllexport) LRESULT messageProc(UINT, WPARAM, LPARAM)
{
    return TRUE;
}

extern "C" __declspec(dllexport) BOOL isUnicode()
{
    return TRUE;
}
