import requests
from typing import Any, Dict, List, Optional, Tuple

class ImmichClient:
    def __init__(self, base_url: str, api_key: str, verify_ssl: bool = True) -> None:
        self.base_url = base_url.rstrip("/")
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": api_key, "Content-Type": "application/json"})

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    # --- simple helper to verify connection ---
    def whoami(self) -> Dict[str, Any]:
        try:
            r = self.session.get(self._url("/api/tags"), verify=self.verify_ssl, timeout=15)
            r.raise_for_status()
            return {"ok": True, "tags_count": len(r.json() or [])}
        except Exception as ex:
            return {"ok": False, "error": str(ex)}

    def get_tag_id_by_name(self, tag_name: str) -> Optional[str]:
        r = self.session.get(self._url("/api/tags"), verify=self.verify_ssl, timeout=30)
        r.raise_for_status()
        for t in r.json() or []:
            if isinstance(t, dict) and (t.get("name") or "").lower() == tag_name.lower():
                return t.get("id")
        return None

    def search_assets_by_tag(
        self,
        tag_id: str,
        page: int = 1,
        size: int = 200,
        filename_contains: Optional[str] = None,  # not sent to API; kept local in app.py
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "page": page,
            "size": size,
            "tags": [tag_id],
            "tagIds": [tag_id],
            "withTagIds": [tag_id],
            "isTrashed": False,
        }
        r = self.session.post(self._url("/api/search/metadata"), json=payload, verify=self.verify_ssl, timeout=60)
        r.raise_for_status()
        data = r.json() or {}
        assets = (data.get("assets") or {}).get("items") or []
        debug = {
            "request": payload,
            "endpoint": "/api/search/metadata",
            "status": r.status_code,
            "assets_count": len(assets),
        }
        return assets, debug

    def update_asset_datetime(
        self,
        asset_id: str,
        date_time_original_iso: Optional[str] = None,
        file_created_at_iso: Optional[str] = None,
        file_modified_at_iso: Optional[str] = None,
    ) -> bool:
        """
        Update asset datetime using PUT /assets/{id} endpoint.
        According to official API: https://api.immich.app/endpoints/assets/updateAsset
        Only dateTimeOriginal is officially supported, but we keep fileCreatedAt/fileModifiedAt
        as optional parameters in case they're accepted by the server.
        """
        payload: Dict[str, Any] = {}
        if date_time_original_iso:
            payload["dateTimeOriginal"] = date_time_original_iso
        # Note: fileCreatedAt and fileModifiedAt are not in official API spec,
        # but may be accepted by some Immich versions
        if file_created_at_iso:
            payload["fileCreatedAt"] = file_created_at_iso
        if file_modified_at_iso:
            payload["fileModifiedAt"] = file_modified_at_iso

        # Use PUT method as per official API specification
        r = self.session.put(self._url(f"/api/assets/{asset_id}"), json=payload, verify=self.verify_ssl, timeout=30)
        r.raise_for_status()
        return True

    def remove_tag_from_asset(self, asset_id: str, tag_id: str) -> bool:
        """
        Remove a tag from an asset.
        Immich API endpoint: DELETE /api/tags/{tagId}/assets
        Payload: {"ids": ["assetId"]}
        """
        payload = {"ids": [asset_id]}
        r = self.session.delete(
            self._url(f"/api/tags/{tag_id}/assets"),
            json=payload,
            verify=self.verify_ssl,
            timeout=30
        )
        r.raise_for_status()
        return True
