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
#include <string>
#include <vector>
#include <map>
#include <set>
#include <cstring>

#include "PluginInterface.h"
#include "Scintilla.h"
#include "Notepad_plus_msgs.h"

#include "en16931_data.h"

// --------------------------------------------------------------------------
//  Etat global du plugin
// --------------------------------------------------------------------------
static NppData s_npp;
static FuncItem s_funcItems[1];
static bool s_enabled = false;

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
//  6. Interface plugin Notepad++
// ==========================================================================
BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH) {
        lstrcpy(s_funcItems[0]._itemName, TEXT("Activer / desactiver Facturix"));
        s_funcItems[0]._pFunc = toggleFacturix;
        s_funcItems[0]._init2Check = false;
        s_funcItems[0]._pShKey = NULL;
        s_funcItems[0]._cmdID = 0;
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
    *nbF = 1;
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
