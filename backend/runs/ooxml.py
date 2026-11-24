"""Documents Office (xlsx, docx, pptx) écrits à la main avec la bibliothèque standard : une archive ZIP de fichiers XML.

Volontairement simples (un tableau, du texte titré, des diapositives titre + puces) : pas de dépendance ajoutée, aucune macro,
aucun lien externe ni objet incorporé. Tout texte est échappé ; les caractères interdits en XML sont retirés."""

import io
import re
import zipfile
from xml.sax.saxutils import escape

_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")
NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def x(value) -> str:
    return escape(_BAD.sub("", "" if value is None else str(value)))


def _zip(parts: dict[str, str], media: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in parts.items():  # [Content_Types].xml en premier, comme le font les applications Office
            z.writestr(name, HEAD + body)
        for name, raw in (media or {}).items():
            z.writestr(name, raw)
    return buf.getvalue()


def _rels(*items: tuple[str, str, str]) -> str:
    inner = "".join(
        f'<Relationship Id="{i}" Type="{t}" Target="{x(g)}"{" TargetMode=" + chr(34) + "External" + chr(34) if t.endswith("/hyperlink") else ""}/>'
        for i, t, g in items
    )
    return f'<Relationships xmlns="{REL}">{inner}</Relationships>'


def _types(defaults: str, overrides: dict[str, str]) -> str:
    over = "".join(f'<Override PartName="{p}" ContentType="{c}"/>' for p, c in overrides.items())
    return f'<Types xmlns="{CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>{defaults}{over}</Types>'


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def sheet_name(raw) -> str:
    name = re.sub(r"[\[\]:*?/\\]", " ", str(raw or "")).strip().strip("'")[:31]
    return name or "Feuille1"


def xlsx(columns: list[str], rows: list[list], sheet: str = "Feuille1") -> bytes:
    """Un classeur, une feuille : en-têtes en gras, nombres et booléens typés, texte jamais interprété comme une formule."""

    def cell(ref, v, style=0):
        s = f' s="{style}"' if style else ""
        if isinstance(v, bool):
            return f'<c r="{ref}"{s} t="b"><v>{int(v)}</v></c>'
        if isinstance(v, (int, float)) and v == v and abs(v) != float("inf"):
            return f'<c r="{ref}"{s}><v>{v!r}</v></c>'
        if v is None or v == "":
            return f'<c r="{ref}"{s}/>' if style else ""
        return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{x(v)}</t></is></c>'

    body = ['<row r="1">' + "".join(cell(f"{_col(i)}1", c, 1) for i, c in enumerate(columns)) + "</row>"]
    for n, row in enumerate(rows, 2):
        body.append(f'<row r="{n}">' + "".join(cell(f"{_col(i)}{n}", v) for i, v in enumerate(row)) + "</row>")
    widths = [
        max([len(str(c))] + [len(str(r[i])) for r in rows[:200] if i < len(r) and r[i] is not None])
        for i, c in enumerate(columns)
    ]
    cols = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{min(max(w + 2, 8), 60)}" customWidth="1"/>'
        for i, w in enumerate(widths)
    )
    sheet_xml = (
        f'<worksheet xmlns="{NS_MAIN}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f"<cols>{cols}</cols><sheetData>{''.join(body)}</sheetData></worksheet>"
    )
    styles = (
        f'<styleSheet xmlns="{NS_MAIN}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'
    )
    workbook = f'<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_R}"><sheets><sheet name="{x(sheet_name(sheet))}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    ms = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    return _zip(
        {
            "[Content_Types].xml": _types(
                "",
                {
                    "/xl/workbook.xml": f"{ms}.sheet.main+xml",
                    "/xl/worksheets/sheet1.xml": f"{ms}.worksheet+xml",
                    "/xl/styles.xml": f"{ms}.styles+xml",
                },
            ),
            "_rels/.rels": _rels(("rId1", f"{RT}/officeDocument", "xl/workbook.xml")),
            "xl/workbook.xml": workbook,
            "xl/_rels/workbook.xml.rels": _rels(
                ("rId1", f"{RT}/worksheet", "worksheets/sheet1.xml"), ("rId2", f"{RT}/styles", "styles.xml")
            ),
            "xl/styles.xml": styles,
            "xl/worksheets/sheet1.xml": sheet_xml,
        }
    )


def _docx_styles() -> str:
    def style(sid, name, size, bold=False, before=0):
        b = "<w:b/>" if bold else ""
        return (
            f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/><w:basedOn w:val="Normal"/><w:qFormat/>'
            f'<w:pPr><w:spacing w:before="{before}" w:after="120"/></w:pPr><w:rPr>{b}<w:sz w:val="{size}"/></w:rPr></w:style>'
        )

    return (
        f'<w:styles xmlns:w="{NS_W}"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="120"/></w:pPr></w:style>'
        + style("Title", "Title", 44, True)
        + style("Heading1", "heading 1", 32, True, 240)
        + style("Heading2", "heading 2", 26, True, 200)
        + style("Heading3", "heading 3", 24, True, 160)
        + "</w:styles>"
    )


def _docx_package(
    body: str,
    rels: list[tuple[str, str, str]] | None = None,
    media: dict[str, bytes] | None = None,
    ext_defaults: str = "",
) -> bytes:
    wm = "application/vnd.openxmlformats-officedocument.wordprocessingml"
    return _zip(
        {
            "[Content_Types].xml": _types(
                ext_defaults, {"/word/document.xml": f"{wm}.document.main+xml", "/word/styles.xml": f"{wm}.styles+xml"}
            ),
            "_rels/.rels": _rels(("rId1", f"{RT}/officeDocument", "word/document.xml")),
            "word/document.xml": f'<w:document xmlns:w="{NS_W}" xmlns:r="{NS_R}" xmlns:wp="{NS_WP}" xmlns:a="{NS_A}" xmlns:pic="{NS_PIC}"><w:body>{body}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1417" w:right="1417" w:bottom="1417" w:left="1417" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr></w:body></w:document>',
            "word/_rels/document.xml.rels": _rels(("rId1", f"{RT}/styles", "styles.xml"), *(rels or [])),
            "word/styles.xml": _docx_styles(),
        },
        media,
    )


def _para(t, style=None):
    p = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f'<w:p>{p}<w:r><w:t xml:space="preserve">{x(t)}</w:t></w:r></w:p>'


def docx(title: str, text: str) -> bytes:
    """Document Word : titre, puis le texte ; `# ` et `## ` en début de ligne font des intertitres, `- ` une puce (« • »)."""
    paras = [_para(title, "Title")] if title else []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            paras.append(_para(line[3:], "Heading2"))
        elif line.startswith("# "):
            paras.append(_para(line[2:], "Heading1"))
        elif line.startswith("- "):
            paras.append(_para("• " + line[2:]))
        else:
            paras.append(_para(line))
    return _docx_package("".join(paras))


NS_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
NS_PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
MAX_IMAGE_EMU = 5_580_000  # 15,5 cm : la largeur utile d'une page A4


class _DocxModel:
    """Convertit les blocs du moteur Markdown (workflows.model_md) en Word : titres, gras/italique/barré/code, liens, listes, citations,
    tableaux, filets et images (PNG, JPEG, GIF) intégrées au document."""

    def __init__(self, images: dict):
        self.images = images
        self.rels: list[tuple[str, str, str]] = []
        self.media: dict[str, bytes] = {}
        self.ext: set[str] = set()
        self.link_ids: dict[str, str] = {}

    def run(self, text, bold=False, italic=False, strike=False, code=False, link=False) -> str:
        pr = ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "") + ("<w:strike/>" if strike else "")
        pr += '<w:rFonts w:ascii="Courier New" w:hAnsi="Courier New"/>' if code else ""
        pr += '<w:color w:val="1D4ED8"/><w:u w:val="single"/>' if link else ""
        return f'<w:r><w:rPr>{pr}</w:rPr><w:t xml:space="preserve">{x(text)}</w:t></w:r>'

    def image(self, image_id: str, alt: str) -> str:
        img = self.images.get(image_id)
        if img is None:
            return self.run(alt)
        ext = {"image/png": "png", "image/jpeg": "jpeg", "image/gif": "gif"}[img.content_type]
        name = f"image{len(self.media) + 1}.{ext}"
        self.media[f"word/media/{name}"] = bytes(img.data)
        self.ext.add(ext)
        rid = f"rId{len(self.rels) + 10}"
        self.rels.append((rid, f"{RT}/image", f"media/{name}"))
        w, h = img.width * 9525, img.height * 9525
        if w > MAX_IMAGE_EMU:
            w, h = MAX_IMAGE_EMU, int(h * MAX_IMAGE_EMU / w)
        n = len(self.media)
        return (
            f'<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{w}" cy="{h}"/><wp:docPr id="{n}" name="Image {n}" descr="{x(alt)}"/>'
            f'<a:graphic><a:graphicData uri="{NS_PIC}"><pic:pic><pic:nvPicPr><pic:cNvPr id="{n}" name="{name}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{w}" cy="{h}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
        )

    def link(self, url: str) -> str | None:
        from workflows.model_md import SAFE_LINK

        if not SAFE_LINK.match(url):
            return None
        if url not in self.link_ids:
            rid = f"rId{len(self.rels) + 10}"
            self.link_ids[url] = rid
            self.rels.append((rid, f"{RT}/hyperlink", url))
        return self.link_ids[url]

    def inlines(self, nodes, bold=False, italic=False, strike=False) -> str:
        from workflows.model_md import IMAGE_RE

        out = []
        for n in nodes:
            k = n[0]
            if k == "text":
                out.append(self.run(n[1], bold, italic, strike))
            elif k == "code":
                out.append(self.run(n[1], bold, italic, strike, code=True))
            elif k in ("strong", "em", "del"):
                out.append(self.inlines(n[1], bold or k == "strong", italic or k == "em", strike or k == "del"))
            elif k == "br":
                out.append("<w:r><w:br/></w:r>")
            elif k == "image":
                m = IMAGE_RE.match(n[2])
                out.append(self.image(m.group(1), n[1]) if m else self.run(n[1]))
            elif k == "link":
                rid = self.link(n[1])
                inner = "".join(self.run(t, bold, italic, strike, link=True) for t in [self._plain(n[2])])
                out.append(
                    f'<w:hyperlink r:id="{rid}">{inner}</w:hyperlink>'
                    if rid
                    else self.inlines(n[2], bold, italic, strike)
                )
        return "".join(out)

    def _plain(self, nodes) -> str:
        from workflows.model_md import inline_text

        return inline_text(nodes)

    def para(self, content: str, style=None, indent=0, extra="") -> str:
        ppr = (
            (f'<w:pStyle w:val="{style}"/>' if style else "")
            + extra
            + (f'<w:ind w:left="{indent}"/>' if indent else "")
        )
        return f"<w:p><w:pPr>{ppr}</w:pPr>{content}</w:p>"

    def block(self, b, indent=0) -> str:
        k = b[0]
        if k == "heading":
            return self.para(self.inlines(b[2]), f"Heading{min(b[1], 3)}", indent)
        if k == "para":
            return self.para(self.inlines(b[1]), None, indent)
        if k == "hr":
            return self.para(
                "", None, indent, '<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="9CA3AF"/></w:pBdr>'
            )
        if k == "code":
            return "".join(
                self.para(
                    self.run(ln, code=True), None, indent + 240, '<w:shd w:val="clear" w:color="auto" w:fill="F3F4F6"/>'
                )
                for ln in b[2].split("\n")
            )
        if k == "quote":
            return "".join(self.block(sub, indent + 480) for sub in b[1])
        if k in ("ul", "ol"):
            return self.items(b, indent)
        if k == "table":
            return self.table(b)
        return ""

    def items(self, b, indent) -> str:
        out = []
        for n, item in enumerate(b[2], b[1]):
            mark = "• " if b[0] == "ul" else f"{n}. "
            for j, sub in enumerate(item):
                if sub[0] == "para":
                    out.append(self.para(self.run(mark if j == 0 else "") + self.inlines(sub[1]), None, indent + 360))
                else:
                    out.append(self.block(sub, indent + 360))
        return "".join(out)

    def table(self, b) -> str:
        def cell(content, head):
            runs = self.inlines(content, bold=head)
            return f'<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/>{'<w:shd w:val="clear" w:color="auto" w:fill="E5E7EB"/>' if head else ""}</w:tcPr><w:p>{runs}</w:p></w:tc>'

        borders = "".join(
            f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="9CA3AF"/>'
            for s in ("top", "left", "bottom", "right", "insideH", "insideV")
        )
        rows = ["<w:tr>" + "".join(cell(c, True) for c in b[1]) + "</w:tr>"]
        rows += ["<w:tr>" + "".join(cell(c, False) for c in r) + "</w:tr>" for r in b[3]]
        return f'<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>{borders}</w:tblBorders></w:tblPr><w:tblGrid/>{"".join(rows)}</w:tbl><w:p/>'


def docx_model(title: str, blocks: list, images: dict) -> bytes:
    """Document Word riche depuis les blocs d'un modèle ; `images` : identifiant → objet avec `content_type`, `data`, `width`, `height`."""
    conv = _DocxModel(images)
    body = ([_para(title, "Title")] if title else []) + [conv.block(b) for b in blocks]
    defaults = "".join(f'<Default Extension="{e}" ContentType="image/{e}"/>' for e in sorted(conv.ext))
    return _docx_package("".join(body), conv.rels, conv.media, defaults)


_THEME = (
    f'<a:theme xmlns:a="{NS_A}" name="Fluxonata"><a:themeElements><a:clrScheme name="Fluxonata">'
    '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
    '<a:dk2><a:srgbClr val="1F2937"/></a:dk2><a:lt2><a:srgbClr val="F3F4F6"/></a:lt2>'
    '<a:accent1><a:srgbClr val="2563EB"/></a:accent1><a:accent2><a:srgbClr val="0F766E"/></a:accent2><a:accent3><a:srgbClr val="9A3412"/></a:accent3>'
    '<a:accent4><a:srgbClr val="7C3AED"/></a:accent4><a:accent5><a:srgbClr val="BE123C"/></a:accent5><a:accent6><a:srgbClr val="4D7C0F"/></a:accent6>'
    '<a:hlink><a:srgbClr val="2563EB"/></a:hlink><a:folHlink><a:srgbClr val="7C3AED"/></a:folHlink></a:clrScheme>'
    '<a:fontScheme name="Fluxonata"><a:majorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
    '<a:fmtScheme name="Fluxonata"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
    '<a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="28575"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
    "<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>"
    '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>'
    "</a:themeElements></a:theme>"
)
_GROUP = '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'


def split_slides(text: str, title: str = "") -> list[tuple[str, list[str]]]:
    """Diapositives séparées par une ligne `---` : la première ligne est le titre, les suivantes des puces. Sans séparateur : une seule diapositive.
    Le `title` du nœud, s'il y en a un, ouvre le diaporama."""
    slides = []
    for block in re.split(r"(?m)^\s*---\s*$", text.replace("\r\n", "\n")):
        lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
        if lines:
            slides.append((lines[0].lstrip("# ").strip(), [ln.lstrip("-•* ").strip() for ln in lines[1:]]))
    return ([(title, [])] if title else []) + slides


def _pptx_package(
    slides: list[tuple[str, list[tuple[str, str, str]]]], media: dict[str, bytes] | None = None, defaults: str = ""
) -> bytes:
    """Le paquet PowerPoint : `slides` = (XML de la diapositive, relations supplémentaires : images, liens)."""
    pm = "application/vnd.openxmlformats-officedocument.presentationml"
    over = {
        "/ppt/presentation.xml": f"{pm}.presentation.main+xml",
        "/ppt/slideMasters/slideMaster1.xml": f"{pm}.slideMaster+xml",
        "/ppt/slideLayouts/slideLayout1.xml": f"{pm}.slideLayout+xml",
        "/ppt/theme/theme1.xml": "application/vnd.openxmlformats-officedocument.theme+xml",
    }
    parts: dict[str, str] = {}
    prels = [("rId1", f"{RT}/slideMaster", "slideMasters/slideMaster1.xml")]
    ids = []
    for n, (xml, rels) in enumerate(slides or [(_slide_xml(""), [])], 1):
        parts[f"ppt/slides/slide{n}.xml"] = xml
        parts[f"ppt/slides/_rels/slide{n}.xml.rels"] = _rels(
            ("rId1", f"{RT}/slideLayout", "../slideLayouts/slideLayout1.xml"), *rels
        )
        over[f"/ppt/slides/slide{n}.xml"] = f"{pm}.slide+xml"
        prels.append((f"rId{n + 1}", f"{RT}/slide", f"slides/slide{n}.xml"))
        ids.append(f'<p:sldId id="{255 + n}" r:id="rId{n + 1}"/>')
    prels.append((f"rId{len(ids) + 2}", f"{RT}/theme", "theme/theme1.xml"))
    master = (
        f'<p:sldMaster xmlns:a="{NS_A}" xmlns:r="{NS_R}" xmlns:p="{NS_P}"><p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/></p:bgPr></p:bg><p:spTree>{_GROUP}</p:spTree></p:cSld>'
        '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>'
    )
    layout = f'<p:sldLayout xmlns:a="{NS_A}" xmlns:r="{NS_R}" xmlns:p="{NS_P}" type="blank" preserve="1"><p:cSld name="Vide"><p:spTree>{_GROUP}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'
    pres = (
        f'<p:presentation xmlns:a="{NS_A}" xmlns:r="{NS_R}" xmlns:p="{NS_P}"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        f'<p:sldIdLst>{"".join(ids)}</p:sldIdLst><p:sldSz cx="9144000" cy="6858000" type="screen4x3"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>'
    )
    return _zip(
        {
            "[Content_Types].xml": _types(defaults, over),
            "_rels/.rels": _rels(("rId1", f"{RT}/officeDocument", "ppt/presentation.xml")),
            "ppt/presentation.xml": pres,
            "ppt/_rels/presentation.xml.rels": _rels(*prels),
            "ppt/slideMasters/slideMaster1.xml": master,
            "ppt/slideMasters/_rels/slideMaster1.xml.rels": _rels(
                ("rId1", f"{RT}/slideLayout", "../slideLayouts/slideLayout1.xml"),
                ("rId2", f"{RT}/theme", "../theme/theme1.xml"),
            ),
            "ppt/slideLayouts/slideLayout1.xml": layout,
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _rels(
                ("rId1", f"{RT}/slideMaster", "../slideMasters/slideMaster1.xml")
            ),
            "ppt/theme/theme1.xml": _THEME,
            **parts,
        },
        media,
    )


def _slide_xml(shapes: str) -> str:
    return f'<p:sld xmlns:a="{NS_A}" xmlns:r="{NS_R}" xmlns:p="{NS_P}"><p:cSld><p:spTree>{_GROUP}{shapes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'


def _text_box(sid, name, x0, y0, cx, cy, paras, size, bold=False, bullet=False, color="1F2937") -> str:
    b = ' b="1"' if bold else ""
    bu = '<a:buChar char="•"/>' if bullet else "<a:buNone/>"
    ind = ' marL="342900" indent="-342900"' if bullet else ""
    ps = (
        "".join(
            f'<a:p><a:pPr{ind}>{bu}</a:pPr><a:r><a:rPr lang="fr-FR" sz="{size}"{b}><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:rPr><a:t>{x(t)}</a:t></a:r></a:p>'
            for t in paras
        )
        or '<a:p><a:endParaRPr lang="fr-FR"/></a:p>'
    )
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="{x0}" y="{y0}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr><p:txBody><a:bodyPr wrap="square" rtlCol="0"><a:normAutofit/></a:bodyPr><a:lstStyle/>{ps}</p:txBody></p:sp>'
    )


def pptx(slides: list[tuple[str, list[str]]]) -> bytes:
    """Diaporama simple (titre + puces) depuis un texte brut."""
    out: list[tuple[str, list[tuple[str, str, str]]]] = []
    for n, (title, bullets) in enumerate(slides or [("", [])], 1):
        if n == 1 and not bullets:
            shapes = _text_box(2, "Titre", 685800, 2286000, 7772400, 1143000, [title], 4000, True)
        else:
            shapes = _text_box(
                2, "Titre", 457200, 304800, 8229600, 914400, [title], 3200, True, color="2563EB"
            ) + _text_box(3, "Contenu", 457200, 1371600, 8229600, 4876800, bullets, 2000, bullet=True)
        out.append((_slide_xml(shapes), []))
    return _pptx_package(out)


# ------------------------------------------------------------------ PowerPoint depuis un modèle de document

EMU_PT = 12700
SLIDE_W, SLIDE_H, SLIDE_MARGIN = 9144000, 6858000, 457200
BODY_TOP, BODY_BOTTOM = 1371600, SLIDE_H - 365760


class _SlideBuilder:
    """Une diapositive : un titre, puis des blocs empilés (texte, tableaux, images) ; trop plein, la suite passe sur une diapositive « suite »."""

    def __init__(self, title: str, images: dict, cover: bool = False):
        self.title, self.images, self.cover = title, images, cover
        self.shapes: list[str] = []
        self.rels: list[tuple[str, str, str]] = []
        self.media: dict[str, bytes] = {}
        self.ext: set[str] = set()
        self.y = BODY_TOP
        self.next_id = 3

    def sid(self) -> int:
        self.next_id += 1
        return self.next_id

    def rid(self, kind: str, target: str) -> str:
        rid = f"rId{len(self.rels) + 2}"
        self.rels.append((rid, f"{RT}/{kind}", target))
        return rid

    def run(self, text, size, bold=False, italic=False, strike=False, code=False, url=None, color="1F2937") -> str:
        attrs = (
            f'lang="fr-FR" sz="{size}"'
            + (' b="1"' if bold else "")
            + (' i="1"' if italic else "")
            + (' strike="sngStrike"' if strike else "")
        )
        link = f'<a:hlinkClick r:id="{self.rid("hyperlink", url)}"/>' if url else ""
        font = '<a:latin typeface="Courier New"/>' if code else ""
        fill = f'<a:solidFill><a:srgbClr val="{"1D4ED8" if url else color}"/></a:solidFill>'
        return f"<a:r><a:rPr {attrs}>{fill}{font}{link}</a:rPr><a:t>{x(text)}</a:t></a:r>"

    def runs(self, nodes, size, bold=False, italic=False, strike=False, color="1F2937", url=None) -> str:
        from workflows.model_md import SAFE_LINK, inline_text

        out = []
        for n in nodes:
            k = n[0]
            if k in ("text", "code"):
                out.append(self.run(n[1], size, bold, italic, strike, k == "code", url, color))
            elif k in ("strong", "em", "del"):
                out.append(
                    self.runs(n[1], size, bold or k == "strong", italic or k == "em", strike or k == "del", color, url)
                )
            elif k == "link":
                out.append(self.runs(n[2], size, bold, italic, strike, color, n[1] if SAFE_LINK.match(n[1]) else url))
            elif k == "br":
                out.append("<a:br/>")
            elif k == "image":
                out.append(self.run(f"[{n[1] or 'image'}]", size, italic=True, color="4B5563"))
        return "".join(out) or self.run(inline_text(nodes), size)

    # -- paragraphes
    def para(self, nodes, size, level=0, bullet: str | None = None, bold=False, italic=False, color="1F2937") -> str:
        indent = 342900 + level * 342900
        bu = f'<a:buChar char="{bullet}"/>' if bullet else "<a:buNone/>"
        pad = f' marL="{indent}" indent="-342900"' if bullet else ""
        return f'<a:p><a:pPr{pad}><a:spcBef><a:spcPts val="600"/></a:spcBef>{bu}</a:pPr>{self.runs(nodes, size, bold, italic, color=color)}</a:p>'

    def block_paras(self, b: tuple, level: int = 0) -> list[str]:
        k = b[0]
        if k == "para":
            return [self.para(b[1], 1800, level)]
        if k == "heading":
            return [self.para(b[2], 2200 if b[1] <= 2 else 2000, level, bold=True, color="111827")]
        if k == "quote":
            return [p for sub in b[1] for p in self._quoted(sub, level)]
        if k == "code":
            return [
                f'<a:p><a:r><a:rPr lang="fr-FR" sz="1400"><a:latin typeface="Courier New"/></a:rPr><a:t>{x(line)}</a:t></a:r></a:p>'
                for line in b[2].split("\n")
            ]
        if k in ("ul", "ol"):
            return self.list_paras(b, level)
        return []

    def _quoted(self, sub: tuple, level: int) -> list[str]:
        if sub[0] == "para":
            return [self.para(sub[1], 1800, level + 1, italic=True, color="4B5563")]
        return self.block_paras(sub, level + 1)

    def list_paras(self, b: tuple, level: int) -> list[str]:
        out = []
        for n, item in enumerate(b[2], b[1]):
            for j, sub in enumerate(item):
                if j == 0 and sub[0] == "para":
                    out.append(
                        self.para(sub[1], 1800, level, "•" if b[0] == "ul" else None)
                        if b[0] == "ul"
                        else self.para([("text", f"{n}. ")] + list(sub[1]), 1800, level)
                    )
                else:
                    out += self.block_paras(sub, level + 1)
        return out

    # -- formes
    def lines_height(self, paras: list[str], size_pt: int = 18) -> int:
        return int(max(len(paras), 1) * size_pt * 1.55 * EMU_PT)

    def add_text(self, paras: list[str]) -> bool:
        height = self.lines_height(paras)
        if self.y + height > BODY_BOTTOM and self.shapes:
            return False
        sid = self.sid()
        self.shapes.append(
            f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="Texte {sid}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="{SLIDE_MARGIN}" y="{self.y}"/><a:ext cx="{SLIDE_W - 2 * SLIDE_MARGIN}" cy="{height}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr><p:txBody><a:bodyPr wrap="square" rtlCol="0"><a:normAutofit/></a:bodyPr><a:lstStyle/>{"".join(paras)}</p:txBody></p:sp>'
        )
        self.y += height + 91440
        return True

    def add_image(self, image_id: str, alt: str) -> bool:
        img = self.images.get(image_id)
        if img is None:
            return self.add_text([self.para([("text", f"[{alt or 'image'}]")], 1800, italic=True, color="4B5563")])
        room_h, room_w = BODY_BOTTOM - self.y, SLIDE_W - 2 * SLIDE_MARGIN
        w, h = img.width * 9525, img.height * 9525
        scale = min(1.0, room_w / w, room_h / h)
        if scale < 0.25 and self.shapes:
            return False  # trop à l'étroit : on passe à la diapositive suivante
        scale = min(1.0, room_w / w, (BODY_BOTTOM - BODY_TOP) / h) if scale < 0.25 else scale
        w, h = int(w * scale), int(h * scale)
        ext = {"image/png": "png", "image/jpeg": "jpeg", "image/gif": "gif"}[img.content_type]
        name = f"slide-img-{image_id[:8]}.{ext}"
        self.media[f"ppt/media/{name}"] = bytes(img.data)
        self.ext.add(ext)
        rid, sid = self.rid("image", f"../media/{name}"), self.sid()
        self.shapes.append(
            f'<p:pic><p:nvPicPr><p:cNvPr id="{sid}" name="Image {sid}" descr="{x(alt)}"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
            f'<p:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="{SLIDE_MARGIN}" y="{self.y}"/><a:ext cx="{w}" cy="{h}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
        )
        self.y += h + 91440
        return True

    def add_table(self, b: tuple) -> bool:
        cols, row_h = len(b[1]), 370840
        height = row_h * (1 + len(b[3]))
        if self.y + height > BODY_BOTTOM and self.shapes:
            return False
        width = SLIDE_W - 2 * SLIDE_MARGIN
        edge = "".join(
            f'<a:{e} w="9525"><a:solidFill><a:srgbClr val="9CA3AF"/></a:solidFill></a:{e}>'
            for e in ("lnL", "lnR", "lnT", "lnB")
        )

        def cell(content, head, align):
            algn = {"center": "ctr", "right": "r"}.get(align, "l")
            fill = '<a:solidFill><a:srgbClr val="E5E7EB"/></a:solidFill>' if head else ""
            return f'<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:pPr algn="{algn}"/>{self.runs(content, 1400, bold=head)}</a:p></a:txBody><a:tcPr>{edge}{fill}</a:tcPr></a:tc>'

        aligns = [*b[2], *[""] * cols]
        rows = [f'<a:tr h="{row_h}">' + "".join(cell(c, True, aligns[j]) for j, c in enumerate(b[1])) + "</a:tr>"]
        rows += [
            f'<a:tr h="{row_h}">' + "".join(cell(c, False, aligns[j]) for j, c in enumerate(r)) + "</a:tr>"
            for r in b[3]
        ]
        grid = "".join(f'<a:gridCol w="{width // cols}"/>' for _ in range(cols))
        sid = self.sid()
        self.shapes.append(
            f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{sid}" name="Tableau {sid}"/><p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr><p:nvPr/></p:nvGraphicFramePr>'
            f'<p:xfrm><a:off x="{SLIDE_MARGIN}" y="{self.y}"/><a:ext cx="{width}" cy="{height}"/></p:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
            f'<a:tbl><a:tblPr firstRow="1"/><a:tblGrid>{grid}</a:tblGrid>{"".join(rows)}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>'
        )
        self.y += height + 137160
        return True

    def add(self, piece: tuple) -> bool:
        from workflows.model_md import IMAGE_RE

        kind, value = piece
        if kind == "table":
            return self.add_table(value)
        if kind == "image":
            m = IMAGE_RE.match(value[2])
            return (
                self.add_image(m.group(1), value[1])
                if m
                else self.add_text([self.para([("text", f"[{value[1]}]")], 1800, italic=True)])
            )
        paras = self.block_paras(value)
        return self.add_text(paras) if paras else True

    def title_shape(self, text: str, suffix: str = "") -> str:
        if self.cover:
            return _text_box(2, "Titre", 685800, 2286000, 7772400, 1143000, [text], 4000, True)
        return _text_box(
            2,
            "Titre",
            SLIDE_MARGIN,
            304800,
            SLIDE_W - 2 * SLIDE_MARGIN,
            914400,
            [text + suffix],
            3200,
            True,
            color="2563EB",
        )


def _slide_blocks(blocks: list, title: str, images: dict) -> list[_SlideBuilder]:
    """Les diapositives d'un groupe de blocs : le premier titre devient le titre ; ce qui ne tient pas passe sur une diapositive « suite »."""
    from workflows.model_md import inline_text

    body = list(blocks)
    first = next((i for i, b in enumerate(body) if b[0] == "heading"), None)
    if first is not None:
        title = inline_text(body.pop(first)[2])
    slides, current = [], _SlideBuilder(title, images)
    for b in body:
        for piece in _pieces(b):
            if not current.add(piece):
                slides.append(current)
                current = _SlideBuilder(title, images)
                current.add(piece)
    slides.append(current)
    return slides


def _pieces(b: tuple) -> list:
    """Un bloc se pose en morceaux : un tableau, une image, ou du texte (les images d'un paragraphe sortent à part)."""
    if b[0] == "table":
        return [("table", b)]
    if b[0] == "para" and any(n[0] == "image" for n in b[1]):
        out: list = []
        run: list = []
        for n in b[1]:
            if n[0] == "image":
                if run:
                    out.append(("block", ("para", run)))
                    run = []
                out.append(("image", n))
            else:
                run.append(n)
        return out + ([("block", ("para", run))] if run else [])
    return [("block", b)]


def pptx_model(title: str, blocks: list, images: dict) -> bytes:
    """Diaporama depuis un modèle : une diapositive par groupe de blocs séparé par un filet `---`, avec mise en forme, tableaux et images."""
    groups: list[list] = [[]]
    for b in blocks:
        if b[0] == "hr":
            groups.append([])
        else:
            groups[-1].append(b)
    built: list[_SlideBuilder] = []
    if title:
        cover = _SlideBuilder(title, images, cover=True)
        built.append(cover)
    for g in groups:
        if g:
            built += _slide_blocks(g, "", images)
    if not built:
        raise ValueError("Aucune diapositive")
    slides, media, ext = [], {}, set()
    seen: dict[str, int] = {}
    for sb in built:
        label = sb.title
        seen[label] = seen.get(label, 0) + 1
        suffix = " (suite)" if seen[label] > 1 and not sb.cover else ""
        slides.append((_slide_xml(sb.title_shape(label, suffix) + "".join(sb.shapes)), sb.rels))
        media.update(sb.media)
        ext |= sb.ext
    defaults = "".join(f'<Default Extension="{e}" ContentType="image/{e}"/>' for e in sorted(ext))
    return _pptx_package(slides, media, defaults)
