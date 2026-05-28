"""Tests del cliente SEACE/PrimeFaces."""

from __future__ import annotations

from .client import SeaceClient


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None


class _Session:
    def __init__(self) -> None:
        self.headers = {}
        self.proxies = {}
        self.post_data: dict[str, str] | None = None

    def get(self, _url: str, timeout: int) -> _Response:
        return _Response(
            """
            <html>
              <body>
                <form id="formBuscador" action="/buscadorPublico.xhtml">
                  <input type="hidden" name="javax.faces.ViewState" value="old-vs" />
                </form>
              </body>
            </html>
            """
        )

    def post(self, _url: str, data: dict[str, str], timeout: int) -> _Response:
        self.post_data = data
        return _Response(
            """<?xml version="1.0" encoding="UTF-8"?>
            <partial-response><changes>
              <update id="formBuscador:dtProcesos"><![CDATA[
                <tr data-ri="15">
                  <td>16</td><td>08/05/2026 19:26</td>
                  <td>LP-ABR-7-2026-BCRPLIM-2</td><td></td><td>Bien</td>
                  <td>Adquisición e instalación de sistema inalámbrico de conferencia</td>
                  <td>---</td><td>Soles</td><td>3</td>
                  <td><a id="formBuscador:dtProcesos:15:j_idt51" onclick="PrimeFaces.addSubmitParam('formBuscador',{'formBuscador:dtProcesos:15:j_idt51':'formBuscador:dtProcesos:15:j_idt51','nidConvocatoria':'conv','nidProceso':'1011106','nidSistema':'3','ntipo':'0'});"></a></td>
                </tr>
              ]]></update>
              <update id="j_id1:javax.faces.ViewState:0"><![CDATA[new-vs]]></update>
            </changes></partial-response>
            """
        )


def test_fetch_list_page_posts_primefaces_pagination_payload_and_parses_partial_rows():
    client = SeaceClient("20122476309", 2026)
    fake_session = _Session()
    client.session = fake_session

    _html, soup = client.fetch_list_page(1)
    rows = client.parse_rows(soup)

    assert fake_session.post_data is not None
    assert fake_session.post_data["javax.faces.behavior.event"] == "page"
    assert fake_session.post_data["javax.faces.partial.event"] == "page"
    assert fake_session.post_data["formBuscador:dtProcesos_encodeFeature"] == "true"
    assert fake_session.post_data["formBuscador:dtProcesos_first"] == "15"
    assert client._list_view_state == "new-vs"
    assert len(rows) == 1
    assert rows[0].nomenclatura == "LP-ABR-7-2026-BCRPLIM-2"
    assert rows[0].nid_proceso == "1011106"
