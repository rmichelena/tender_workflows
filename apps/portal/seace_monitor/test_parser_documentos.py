"""Tests para nombres de documentos en ficha SEACE."""

from .parser import parse_ficha


def test_parse_documentos_uses_onclick_not_archivo_size_column():
    html = """
    <html><body>
    <table>
      <tr>
        <td>1</td>
        <td>Convocatoria</td>
        <td>Bases Administrativas</td>
        <td>(2646 KB)</td>
        <td>13/05/2026 18:08</td>
        <td>
          <a onclick="descargaDocGeneral('4dd843e9-845a-47c1-8692-a9e700e49fef','3','Bases_LPA 0012026F.pdf')">
            Descargar
          </a>
        </td>
      </tr>
    </table>
    </body></html>
    """
    ficha = parse_ficha(html, "4dd843e9-845a-47c1-8692-a9e700e49fef", "nid-1")
    assert len(ficha.documentos) == 1
    doc = ficha.documentos[0]
    assert doc.nombre == "Bases_LPA 0012026F.pdf"
    assert doc.nombre != "(2646 KB)"
    assert doc.tipo_documento == "Bases Administrativas"
    assert doc.fecha_publicacion == "13/05/2026 18:08"


def test_parse_documentos_falls_back_to_tipo_when_onclick_empty():
    html = """
    <html><body>
    <table>
      <tr>
        <td>1</td>
        <td>Etapa</td>
        <td>Bases Administrativas</td>
        <td>(1 KB)</td>
        <td>01/01/2026</td>
        <td>
          <a onclick="descargaDocGeneral('uuid-1','3','')">(1 KB)</a>
        </td>
      </tr>
    </table>
    </body></html>
    """
    ficha = parse_ficha(html, "4dd843e9-845a-47c1-8692-a9e700e49fef", "nid-1")
    assert ficha.documentos[0].nombre == "Bases Administrativas"
    assert ficha.documentos[0].tamano_kb == "1"
