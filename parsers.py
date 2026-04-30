"""Parsing RDI (texte colonnes fixes cp1252) et extraction du XML Factur-X embarque dans le PDF."""
import PyPDF2
import io
import pikepdf


def parse_rdi(rdi_path):
    """
    Parse le fichier RDI et retourne (data, articles).
    - data : dict des champs d'en-tête (hors articles)
    - articles : liste de dicts, un par bloc article (BG25/BG26/BG29/BG30/BG31)
    """
    data = {}
    articles = []
    current_article = None
    last_bt21_value = None  # Pour suivre les paires BT21/BT22

    # Tags qui appartiennent aux blocs articles (lignes de facture)
    ARTICLE_TAG_PREFIXES = ('GS_FECT_EINV-BG25-', 'GS_FECT_EINV-BG26-',
                            'GS_FECT_EINV-BG29-', 'GS_FECT_EINV-BG30-',
                            'GS_FECT_EINV-BG31-',
                            'MAIN_GS_FECT_EINV-BG25-', 'MAIN_GS_FECT_EINV-BG26-',
                            'MAIN_GS_FECT_EINV-BG29-', 'MAIN_GS_FECT_EINV-BG30-',
                            'MAIN_GS_FECT_EINV-BG31-')

    # Lire toutes les lignes parsées en une seule passe
    parsed_lines = []  # (record_type, tag, value)
    try:
        with open(rdi_path, 'r', encoding='cp1252') as f:
            for line in f:
                if line.startswith('D'):
                    if len(line) >= 176:
                        try:
                            length_str = line[172:175]
                            length = int(length_str)
                            value = line[175:175+length] if len(line) > 175 else ''
                            tag_section = line[41:172].strip()
                            tag_parts = tag_section.split()
                            if tag_parts:
                                tag = tag_parts[-1]
                                record_type = line.split()[0]
                                parsed_lines.append((record_type, tag, value))
                        except:
                            pass
    except:
        pass

    # Construire data_multi : {tag_upper: [(record_type, value), ...]} pour toutes les occurrences
    data_multi = {}
    for record_type, tag, value in parsed_lines:
        tag_upper = tag.upper()
        if tag_upper not in data_multi:
            data_multi[tag_upper] = []
        data_multi[tag_upper].append((record_type, value))

    # Passe 1 : construire data normalement et collecter les valeurs BT-22 qui sont des références
    bt22_refs = set()  # Noms de tags référencés par les BT-22 (ex: "PENALITE-TEXT", "TTAUX-TEXT")
    for record_type, tag, value in parsed_lines:
        # Gestion spéciale des paires BT21/BT22 (multiples occurrences)
        if tag == 'GS_FECT_EINV-BG1-BT21':
            suffix = value.strip().upper()
            last_bt21_value = suffix
            suffixed_tag = f'{tag}-{suffix}'
            data[suffixed_tag] = value
        elif tag == 'GS_FECT_EINV-BG1-BT22' and last_bt21_value:
            suffixed_tag = f'{tag}-{last_bt21_value}'
            data[suffixed_tag] = value
            last_bt21_value = None
            # Détecter si la valeur est une référence vers un bloc de texte
            val_stripped = value.strip()
            if val_stripped and not val_stripped.startswith('GS_FECT_EINV') and val_stripped.replace('-', '').replace('_', '').isalpha():
                bt22_refs.add(val_stripped)
        # Gestion des blocs articles (BG25/BG26/BG29/BG30/BG31)
        elif any(tag.startswith(p) or tag.upper().startswith(p) for p in ARTICLE_TAG_PREFIXES):
            if 'BT126' in tag:
                current_article = {}
                articles.append(current_article)
            if current_article is not None:
                current_article[tag] = value
        elif tag not in data:
            data[tag] = value

    # Passe 2 : accumuler les blocs de texte référencés par les BT-22
    if bt22_refs:
        text_blocks = {}
        for record_type, tag, value in parsed_lines:
            if tag in bt22_refs:
                if tag not in text_blocks:
                    text_blocks[tag] = []
                text_blocks[tag].append(value)

        # Résolution : remplacer les valeurs BT-22 par le texte concaténé
        for key in list(data.keys()):
            if 'BT22' in key:
                val = data[key].strip()
                if val in text_blocks:
                    data[key] = ' '.join(text_blocks[val])

    return data, articles, data_multi

def extract_xml_from_pdf(pdf_path):
    try:
        with open(pdf_path, 'rb') as f:
            pdf_reader = PyPDF2.PdfReader(f)
            if '/Names' in pdf_reader.trailer['/Root']:
                names = pdf_reader.trailer['/Root']['/Names']
                if '/EmbeddedFiles' in names:
                    embedded = names['/EmbeddedFiles']['/Names']
                    for i in range(0, len(embedded), 2):
                        file_name = embedded[i]
                        if isinstance(file_name, str) and file_name.lower().endswith('.xml'):
                            file_spec = embedded[i + 1].get_object()
                            file_obj = file_spec['/EF']['/F'].get_object()
                            xml_content = file_obj.get_data()
                            return xml_content.decode('utf-8') if isinstance(xml_content, bytes) else xml_content
    except:
        pass
    return None

def remove_pdf_signature(pdf_path):
    """Reconstruit le PDF sans les signatures numériques (champs /Sig, /Perms, SigFlags).
    Les fichiers embarqués (XML Factur-X) sont préservés."""
    with pikepdf.open(pdf_path, allow_overwriting_input=False) as pdf:
        # Supprimer /Perms qui verrouille les modifications
        if '/Perms' in pdf.Root:
            del pdf.Root['/Perms']
        # Supprimer les champs de signature dans AcroForm
        if '/AcroForm' in pdf.Root:
            acroform = pdf.Root['/AcroForm']
            if '/Fields' in acroform:
                acroform['/Fields'] = pikepdf.Array([
                    f for f in acroform['/Fields']
                    if pdf.get_object(f.objgen).get('/FT') != pikepdf.Name('/Sig')
                ])
            if '/SigFlags' in acroform:
                del acroform['/SigFlags']
        output = io.BytesIO()
        pdf.save(output)
        output.seek(0)
        return output


FACTURX_FALLBACK_NS = {
    'rsm': 'urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100',
    'ram': 'urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100',
    'udt': 'urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100',
    'qdt': 'urn:un:unece:uncefact:data:standard:QualifiedDataType:100',
    'xsi': 'http://www.w3.org/2001/XMLSchema-instance',
    'xs':  'http://www.w3.org/2001/XMLSchema',
}

def build_xml_namespaces(xml_doc):
    """
    Construit le dict de namespaces pour evaluer les XPath du mapping.
    On part du fallback Factur-X puis on superpose les declarations du XML
    (root + descendants), en ignorant le namespace par defaut (cle None,
    non utilisable dans XPath 1.0). Tout prefixe present dans le XML sera
    donc reconnu, meme si un mapping ajoute un namespace inattendu.
    """
    ns = dict(FACTURX_FALLBACK_NS)
    if xml_doc is None:
        return ns
    try:
        for el in xml_doc.iter():
            for prefix, uri in (el.nsmap or {}).items():
                if prefix and uri:
                    ns[prefix] = uri
    except Exception:
        pass
    return ns


def get_xml_tag_name(xpath):
    """Extrait le nom complet du dernier tag dans le XPath (ex: 'ram:TypeCode' depuis '//ram:TypeCode')"""
    if not xpath:
        return ''
    # Nettoyer le XPath et récupérer le dernier élément
    xpath = xpath.strip()
    parts = xpath.split('/')
    for part in reversed(parts):
        part = part.strip()
        # Ignorer les parties vides et les conditions entre crochets
        if part and '[' not in part and part != '..':
            return part
    return parts[-1] if parts else ''

def get_xml_short_name(xpath):
    if not xpath:
        return ''
    parts = xpath.split('/')
    for part in reversed(parts):
        if ':' in part:
            return part.split(':')[1]
    return parts[-1] if parts else ''

