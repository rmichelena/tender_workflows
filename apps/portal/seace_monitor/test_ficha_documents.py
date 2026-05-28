"""Tests de paginación de documentos en ficha SEACE."""

from __future__ import annotations

from unittest.mock import MagicMock

from .ficha_documents import (
    _ficha_documentos_pagination,
    collect_ficha_documentos,
)


def _doc_row(uuid: str, nombre: str) -> str:
    return f"""
    <tr>
      <td>1</td><td>Etapa</td><td>{nombre}</td><td>(1 KB)</td><td>01/01/2026</td>
      <td><a onclick="descargaDocGeneral('{uuid}','3','{nombre}')">(1 KB)</a></td>
    </tr>
    """


def _ficha_html(*rows: str, row_count: int, rows_per_page: int = 5) -> str:
    body = "".join(rows)
    script = (
        '$(function(){PrimeFaces.cw("DataTable","widget_tbFicha_dtDocumentos",'
        f'{{id:"tbFicha:dtDocumentos",paginator:{{rows:{rows_per_page},rowCount:{row_count}}}'
        "});});"
    )
    return f"""
    <html><body>
    <form id="tbFicha">
      <input name="javax.faces.ViewState" value="vs-test"/>
      <table><tbody id="tbFicha:dtDocumentos_data">{body}</tbody></table>
      <div id="tbFicha:dtDocumentos_paginator_bottom">
        <span class="ui-paginator-current">1 de 2</span>
      </div>
    </form>
    <script>{script}</script>
    </body></html>
    """


def test_pagination_info_from_primefaces_script():
    soup_html = _ficha_html(_doc_row("a", "Doc A"), row_count=6, rows_per_page=5)
    from bs4 import BeautifulSoup

    total_pages, rows = _ficha_documentos_pagination(BeautifulSoup(soup_html, "lxml"))
    assert total_pages == 2
    assert rows == 5


def test_collect_documentos_single_page_does_not_post():
    html = _ficha_html(_doc_row("uuid-1", "Doc.pdf"), row_count=1)
    session = MagicMock()

    docs = collect_ficha_documentos(html, session, "https://ficha.example/x")

    assert len(docs) == 1
    assert docs[0].uuid == "uuid-1"
    session.post.assert_not_called()


def test_collect_documentos_fetches_additional_pages():
    page1_rows = "".join(_doc_row(f"uuid-{i}", f"Doc{i}.pdf") for i in range(5))
    html = _ficha_html(page1_rows, row_count=6, rows_per_page=5)
    partial = f"""<?xml version='1.0' encoding='UTF-8'?>
    <partial-response><changes>
      <update id="tbFicha:dtDocumentos"><![CDATA[
        {_doc_row("uuid-6", "Doc6.zip")}
      ]]></update>
    </changes></partial-response>"""
    session = MagicMock()
    response = MagicMock()
    response.text = partial
    response.raise_for_status = MagicMock()
    session.post.return_value = response

    docs = collect_ficha_documentos(html, session, "https://ficha.example/x")

    assert len(docs) == 6
    assert {d.uuid for d in docs} == {f"uuid-{i}" for i in range(1, 7)}
    session.post.assert_called_once()
    posted = session.post.call_args.kwargs["data"]
    assert posted["tbFicha:dtDocumentos_first"] == "5"
    assert posted["tbFicha:dtDocumentos_rows"] == "5"
