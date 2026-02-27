import requests
from typing import Any, Dict, List, Optional, Tuple

class ImmichClient:
    """Minimal HTTP client for talking to the Immich REST API.

    All requests are made through a `requests.Session` instance which has
    the provided API key injected as an `x-api-key` header.  Helper methods
    in this class wrap the specific endpoints used by the web application.
    """

    def __init__(self, base_url: str, api_key: str, verify_ssl: bool = True) -> None:
        """Initialise the HTTP session and set default headers.

        `base_url` has trailing slashes stripped to simplify path
        concatenation.  `verify_ssl` is passed directly to requests so that
        the caller can skip certificate validation if needed.
        """
        self.base_url = base_url.rstrip("/")
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": api_key, "Content-Type": "application/json"})

    def _url(self, path: str) -> str:
        # internal helper to ensure the base_url is always prefixed
        return f"{self.base_url}{path}"

    # --- simple helper to verify connection ---
    def whoami(self) -> Dict[str, Any]:
        """Perform a lightweight request to validate the API key and server.

        The route used here is `GET /api/tags` which should be available on
        all Immich installations.  The returned dictionary contains the
        boolean `ok` flag and either a count of tags or an error message.
        """
        try:
            r = self.session.get(self._url("/api/tags"), verify=self.verify_ssl, timeout=15)
            r.raise_for_status()
            return {"ok": True, "tags_count": len(r.json() or [])}
        except Exception as ex:
            return {"ok": False, "error": str(ex)}

    def get_tag_id_by_name(self, tag_name: str) -> Optional[str]:
        """Retrieve the UUID for a given tag name.

        Since the Immich API does not expose a direct lookup endpoint, we
        fetch all tags and perform a case-insensitive match on the name.
        Returns `None` if the tag cannot be found.
        """
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
        """Perform a search request for assets that have the given tag.

        The `filename_contains` argument is purely informational and not sent
        to the server; filtering on the filename is performed by the
        application itself when rendering results.  Returns a tuple of the
        assets list and a small debug payload containing request/response
        information useful for troubleshooting.
        """
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
        # include a small sample of the returned assets in the debug payload
        # so the UI can show structure during troubleshooting
        # Add a debug payload including a one-item sample and the EXIF info
        # for that sample (if present).  This helps inspect returned fields
        # without making extra API calls from the UI.
        assets_sample = assets[:1] if assets else []
        assets_sample_exif = None
        if assets_sample:
            # Some servers include `exifInfo` on the asset; pull it out
            assets_sample_exif = assets_sample[0].get("exifInfo") if isinstance(assets_sample[0], dict) else None

        debug = {
            "request": payload,
            "endpoint": "/api/search/metadata",
            "status": r.status_code,
            "assets_count": len(assets),
            "assets_sample": assets_sample,
            "assets_sample_exif": assets_sample_exif,
        }
        return assets, debug

    def get_asset(self, asset_id: str) -> Dict[str, Any]:
        """Fetch full asset details for a single asset ID.

        The detailed asset endpoint commonly contains `exifInfo` and
        other fields that the search response may omit. Returns a dict
        (the parsed JSON) or an empty dict on unexpected responses.
        """
        r = self.session.get(self._url(f"/api/assets/{asset_id}"), verify=self.verify_ssl, timeout=30)
        r.raise_for_status()
        return r.json() or {}

    def update_asset_metadata(
        self,
        asset_id: str,
        date_time_original_iso: Optional[str] = None,
        file_created_at_iso: Optional[str] = None,
        file_modified_at_iso: Optional[str] = None,
        description: Optional[str] = None,
    ) -> bool:
        """Set new datetime values on an asset.

        The official API normally accepts only `dateTimeOriginal`, but some
        server versions also honour `fileCreatedAt` and `fileModifiedAt`.
        We therefore include them when available.  This method raises an
        exception on HTTP errors, and returns `True` on success.
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
        # Optional description update for bulk metadata operations
        if description is not None:
            payload["description"] = description

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
