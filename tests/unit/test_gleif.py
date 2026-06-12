from unittest.mock import MagicMock, patch

import pytest

from research_platform.sources.gleif import GLEIFClient, GLEIFError, GLEIFRecord


LEGAL_NAME_RESPONSE = {
    "data": [
        {
            "type": "lei-records",
            "id": "213800VCU9TBANZIN455",
            "attributes": {
                "lei": "213800VCU9TBANZIN455",
                "entity": {
                    "legalName": {"name": "THE GYM GROUP PLC", "language": "en"},
                    "legalAddress": {"country": "GB", "city": "LONDON"},
                    "jurisdiction": "GB",
                    "registeredAs": "08528493",
                    "status": "ACTIVE",
                    "otherNames": [
                        {"name": "THE GYM GROUP LIMITED"},
                    ],
                },
                "registration": {
                    "status": "ISSUED",
                },
            },
        }
    ]
}

FUZZY_RESPONSE = {
    "data": [
        {
            "type": "fuzzycompletions",
            "attributes": {"value": "THE GYM GROUP PLC"},
            "relationships": {
                "lei-records": {
                    "data": {"type": "lei-records", "id": "213800VCU9TBANZIN455"},
                }
            },
        }
    ]
}

ISINS_RESPONSE = {
    "data": [
        {
            "type": "isins",
            "attributes": {
                "lei": "213800VCU9TBANZIN455",
                "isin": "GB00BZBX0P70",
            },
        }
    ]
}
SINGLE_RECORD_RESPONSE = {
    "data": LEGAL_NAME_RESPONSE["data"][0]
}


def make_mock_response(json_data, status_code=200):
    response = MagicMock()
    response.json.return_value = json_data
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    return response


class TestGLEIFClient:
    def test_search_company_uses_legal_name_filter(self):
        with patch("research_platform.sources.gleif.httpx.get") as mock_get:
            mock_get.return_value = make_mock_response(LEGAL_NAME_RESPONSE)
            results = GLEIFClient().search_company("The Gym Group", country="GB", limit=5)
        assert len(results) == 1
        assert isinstance(results[0], GLEIFRecord)
        assert results[0].lei == "213800VCU9TBANZIN455"
        _, kwargs = mock_get.call_args
        assert kwargs["params"] == {
            "filter[entity.legalName]": "The Gym Group",
            "page[size]": 5,
            "filter[entity.legalAddress.country]": "GB",
        }

    def test_search_company_falls_back_to_fuzzy_completion(self):
        with patch("research_platform.sources.gleif.httpx.get") as mock_get:
            mock_get.side_effect = [
                make_mock_response({"data": []}),
                make_mock_response(FUZZY_RESPONSE),
                make_mock_response(SINGLE_RECORD_RESPONSE),
            ]
            results = GLEIFClient().search_company("The Gym Group", country="GB", limit=5)
        assert len(results) == 1
        assert results[0].legal_name == "THE GYM GROUP PLC"

    def test_get_isins_returns_related_isins(self):
        with patch("research_platform.sources.gleif.httpx.get") as mock_get:
            mock_get.return_value = make_mock_response(ISINS_RESPONSE)
            isins = GLEIFClient().get_isins("213800VCU9TBANZIN455")
        assert isins == ["GB00BZBX0P70"]

    def test_resolve_company_returns_best_record(self):
        payload = {
            "data": LEGAL_NAME_RESPONSE["data"] + [
                {
                    "type": "lei-records",
                    "id": "00000000000000000000",
                    "attributes": {
                        "lei": "00000000000000000000",
                        "entity": {
                            "legalName": {"name": "GYM GROUP HOLDINGS", "language": "en"},
                            "legalAddress": {"country": "JE", "city": "ST HELIER"},
                            "jurisdiction": "JE",
                            "registeredAs": "000",
                            "status": "ACTIVE",
                            "otherNames": [],
                        },
                        "registration": {"status": "ISSUED"},
                    },
                }
            ]
        }
        with patch("research_platform.sources.gleif.httpx.get") as mock_get:
            mock_get.return_value = make_mock_response(payload)
            result = GLEIFClient().resolve_company("The Gym Group", country="GB")
        assert result.lei == "213800VCU9TBANZIN455"

    def test_search_company_http_error_raises_gleif_error(self):
        import httpx

        with patch("research_platform.sources.gleif.httpx.get") as mock_get:
            mock_get.side_effect = httpx.ConnectError("connection refused")
            with pytest.raises(GLEIFError, match="GLEIF request failed"):
                GLEIFClient().search_company("The Gym Group", country="GB")
