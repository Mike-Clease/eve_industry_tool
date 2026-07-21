from unittest.mock import MagicMock, patch

import httpx
import pytest

from eve_api import (
    _system_to_region,
    price_history,
    region_of_system,
    resolve_system,
    resolve_type,
)

SAMPLE_CSV = (
    "regionID,constellationID,solarSystemID,solarSystemName\n"
    "10000002,20000020,30000142,Jita\n"
    "10000043,20000635,30002813,Niarja\n"
)


def _post_resp(json_data=None, status_code=200):
    r = MagicMock()
    r.json.return_value = json_data or {}
    if status_code >= 400:
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=r
        )
    return r


def _get_resp(json_data=None, status_code=200):
    r = MagicMock()
    r.json.return_value = json_data or []
    if status_code >= 400:
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=r
        )
    return r


def _csv_resp(csv_text=SAMPLE_CSV):
    r = MagicMock()
    r.content.decode.return_value = csv_text
    return r


# ---------------------------------------------------------------------------
# resolve_type
# ---------------------------------------------------------------------------


class TestResolveType:
    def test_returns_int_id(self):
        resp = _post_resp({"inventory_types": [{"id": 34, "name": "Tritanium"}]})
        with patch("eve_api.httpx.post", return_value=resp):
            result = resolve_type("Tritanium")
        assert result == 34
        assert isinstance(result, int)

    def test_posts_name_to_universe_ids(self):
        resp = _post_resp({"inventory_types": [{"id": 34}]})
        with patch("eve_api.httpx.post", return_value=resp) as mock_post:
            resolve_type("Tritanium")
        (url,) = mock_post.call_args.args
        assert "universe/ids" in url
        assert mock_post.call_args.kwargs["json"] == ["Tritanium"]

    def test_multiple_hits_returns_first(self):
        resp = _post_resp({"inventory_types": [{"id": 34}, {"id": 35}]})
        with patch("eve_api.httpx.post", return_value=resp):
            assert resolve_type("Tritanium") == 34

    def test_no_hits_raises_value_error(self):
        resp = _post_resp({"inventory_types": []})
        with patch("eve_api.httpx.post", return_value=resp):
            with pytest.raises(ValueError, match="no type found for 'Unobtainium'"):
                resolve_type("Unobtainium")

    def test_missing_key_raises_value_error(self):
        resp = _post_resp({})
        with patch("eve_api.httpx.post", return_value=resp):
            with pytest.raises(ValueError):
                resolve_type("Unknown")

    def test_http_error_propagates(self):
        with patch("eve_api.httpx.post", return_value=_post_resp(status_code=500)):
            with pytest.raises(httpx.HTTPStatusError):
                resolve_type("Tritanium")


# ---------------------------------------------------------------------------
# resolve_system
# ---------------------------------------------------------------------------


class TestResolveSystem:
    def test_returns_int_id(self):
        resp = _post_resp({"systems": [{"id": 30000142, "name": "Jita"}]})
        with patch("eve_api.httpx.post", return_value=resp):
            result = resolve_system("Jita")
        assert result == 30000142
        assert isinstance(result, int)

    def test_posts_name_to_universe_ids(self):
        resp = _post_resp({"systems": [{"id": 30000142}]})
        with patch("eve_api.httpx.post", return_value=resp) as mock_post:
            resolve_system("Jita")
        (url,) = mock_post.call_args.args
        assert "universe/ids" in url
        assert mock_post.call_args.kwargs["json"] == ["Jita"]

    def test_no_hits_raises_value_error(self):
        resp = _post_resp({"systems": []})
        with patch("eve_api.httpx.post", return_value=resp):
            with pytest.raises(ValueError, match="no system found for 'Fakeville'"):
                resolve_system("Fakeville")

    def test_missing_key_raises_value_error(self):
        resp = _post_resp({})
        with patch("eve_api.httpx.post", return_value=resp):
            with pytest.raises(ValueError):
                resolve_system("Unknown")

    def test_http_error_propagates(self):
        with patch("eve_api.httpx.post", return_value=_post_resp(status_code=404)):
            with pytest.raises(httpx.HTTPStatusError):
                resolve_system("Jita")


# ---------------------------------------------------------------------------
# price_history
# ---------------------------------------------------------------------------


class TestPriceHistory:
    ROWS = [
        {"date": f"2025-{m:02d}-01", "average": float(m * 10)} for m in range(1, 13)
    ]

    def test_returns_list(self):
        with patch("eve_api.httpx.get", return_value=_get_resp(self.ROWS)):
            assert isinstance(price_history(34), list)

    def test_days_slices_from_end(self):
        with patch("eve_api.httpx.get", return_value=_get_resp(self.ROWS)):
            assert price_history(34, days=3) == self.ROWS[-3:]

    def test_default_region_is_jita(self):
        with patch("eve_api.httpx.get", return_value=_get_resp(self.ROWS)) as mock_get:
            price_history(34)
        assert "10000002" in mock_get.call_args.args[0]

    def test_custom_region_in_url(self):
        with patch("eve_api.httpx.get", return_value=_get_resp(self.ROWS)) as mock_get:
            price_history(34, region_id=10000043)
        assert "10000043" in mock_get.call_args.args[0]

    def test_type_id_sent_as_query_param(self):
        with patch("eve_api.httpx.get", return_value=_get_resp(self.ROWS)) as mock_get:
            price_history(587)
        assert mock_get.call_args.kwargs["params"]["type_id"] == 587

    def test_http_error_propagates(self):
        with patch("eve_api.httpx.get", return_value=_get_resp(status_code=404)):
            with pytest.raises(httpx.HTTPStatusError):
                price_history(34)


# ---------------------------------------------------------------------------
# _system_to_region
# ---------------------------------------------------------------------------


class TestSystemToRegion:
    def setup_method(self):
        _system_to_region.cache_clear()

    def test_returns_dict(self):
        with patch("eve_api.httpx.get", return_value=_csv_resp()):
            assert isinstance(_system_to_region(), dict)

    def test_maps_system_id_to_region_id(self):
        with patch("eve_api.httpx.get", return_value=_csv_resp()):
            result = _system_to_region()
        assert result[30000142] == 10000002
        assert result[30002813] == 10000043

    def test_keys_and_values_are_ints(self):
        with patch("eve_api.httpx.get", return_value=_csv_resp()):
            result = _system_to_region()
        for k, v in result.items():
            assert isinstance(k, int) and isinstance(v, int)

    def test_decodes_with_utf8_sig_to_strip_bom(self):
        with patch("eve_api.httpx.get", return_value=_csv_resp()) as mock_get:
            _system_to_region()
        mock_get.return_value.content.decode.assert_called_once_with("utf-8-sig")

    def test_lru_cache_prevents_repeat_download(self):
        with patch("eve_api.httpx.get", return_value=_csv_resp()) as mock_get:
            _system_to_region()
            _system_to_region()
        mock_get.assert_called_once()


# ---------------------------------------------------------------------------
# region_of_system
# ---------------------------------------------------------------------------


class TestRegionOfSystem:
    def setup_method(self):
        _system_to_region.cache_clear()

    def test_returns_correct_region(self):
        with patch("eve_api.httpx.get", return_value=_csv_resp()):
            assert region_of_system(30000142) == 10000002

    def test_unknown_system_raises_key_error(self):
        with patch("eve_api.httpx.get", return_value=_csv_resp()):
            with pytest.raises(KeyError):
                region_of_system(99999999)
