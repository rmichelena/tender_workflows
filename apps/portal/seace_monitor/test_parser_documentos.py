"""Tests para nombres de documentos en ficha SEACE."""

from .parser import parse_ficha


def test_parse_documentos_uses_archivo_column_not_onclick():
    html = """
    <html><body>
    <table>
      <tr>
        <td>1</td>
        <td>Convocatoria</td>
        <td>Bases</td>
        <td>Bases_LPA+0012026F_20260518_123248_485.pdf</td>
        <td>18/05/2026 12:32</td>
        <td>
          <a onclick="descargaDocGeneral('4dd843e9-845a-47c1-8692-a9e700e49fef','3','Bases_LPA 0012026F.pdf')">
            (485 KB)
          </a>
        </td>
      </tr>
    </table>
    </body></html>
    """
    ficha = parse_ficha(html, "4dd843e9-845a-47c1-8692-a9e700e49fef", "nid-1")
    assert len(ficha.documentos) == 1
    doc = ficha.documentos[0]
    assert doc.nombre == "Bases_LPA+0012026F_20260518_123248_485.pdf"
    assert doc.tipo_documento == "Bases"
    assert doc.etapa == "Convocatoria"
    assert doc.fecha_publicacion == "18/05/2026 12:32"


def test_parse_documentos_falls_back_to_onclick_when_archivo_missing():
    html = """
    <html><body>
    <table>
      <tr>
        <td>1</td>
        <td>Etapa</td>
        <td>Doc</td>
        <td></td>
        <td>01/01/2026</td>
        <td>
          <a onclick="descargaDocGeneral('uuid-1','3','fallback.pdf')">(1 KB)</a>
        </td>
      </tr>
    </table>
    </body></html>
    """
    ficha = parse_ficha(html, "4dd843e9-845a-47c1-8692-a9e700e49fef", "nid-1")
    assert ficha.documentos[0].nombre == "fallback.pdf"
