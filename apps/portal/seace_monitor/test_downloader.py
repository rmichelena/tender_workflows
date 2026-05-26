"""Tests para extracción de nombres de descarga Alfresco."""

from .downloader import filename_from_download_path


def test_filename_from_download_path():
    path = (
        "/service/api/node/content/workspace/SpacesStore/"
        "1d8021e2-b946-4077-adc0-984f5332effb/"
        "BASES_SERVIDORESRRRRRRRRRRRR_20260513_180726_833.pdf"
        "?a=true&alf_ticket=TICKET_abc"
    )
    assert (
        filename_from_download_path(path)
        == "BASES_SERVIDORESRRRRRRRRRRRR_20260513_180726_833.pdf"
    )


def test_filename_from_download_path_empty():
    assert filename_from_download_path("") is None
    assert filename_from_download_path("/no/uuid/here") is None
