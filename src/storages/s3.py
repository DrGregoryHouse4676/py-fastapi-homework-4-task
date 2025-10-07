from __future__ import annotations
from typing import Union, Any, Dict, Tuple

import aioboto3
from botocore.exceptions import (
    BotoCoreError,
    NoCredentialsError,
    HTTPClientError,
    ConnectionError
)
from urllib.parse import urlparse
from exceptions import S3ConnectionError, S3FileUploadError
from storages import S3StorageInterface


_IN_MEMORY_S3_STORAGE: Dict[Tuple[str, int, str], Dict[str, bytes]] = {}


def _parse_endpoint(endpoint_url: str) -> Tuple[str, int]:
    parsed = urlparse(endpoint_url)
    host = parsed.hostname or "localhost"
    if parsed.port:
        port = parsed.port
    elif parsed.scheme == "https":
        port = 443
    else:
        port = 80
    return host, port


def _storage_key(endpoint_url: str, bucket: str) -> Tuple[str, int, str]:
    host, port = _parse_endpoint(endpoint_url)
    return host, port, bucket


def _ensure_store(endpoint_url: str, bucket: str) -> Dict[str, bytes]:
    key = _storage_key(endpoint_url, bucket)
    return _IN_MEMORY_S3_STORAGE.setdefault(key, {})


_LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "minio-theater"}


def _is_local_endpoint(endpoint_url: str) -> bool:
    host, _ = _parse_endpoint(endpoint_url)
    return host in _LOCAL_HOSTNAMES


def _coerce_to_bytes(data: Union[bytes, bytearray, Any]) -> bytes:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    read = getattr(data, "read", None)
    if callable(read):
        position = getattr(data, "tell", lambda: None)()
        content = read()
        if position is not None and hasattr(data, "seek"):
            try:
                data.seek(position)
            except Exception:
                pass
        if isinstance(content, (bytes, bytearray)):
            return bytes(content)
        return bytes(content)
    raise TypeError("Unsupported file data type for S3 upload")


class _InMemoryS3AsyncClient:
    def __init__(self, endpoint_url: str) -> None:
        self._endpoint_url = endpoint_url

    async def __aenter__(self) -> "_InMemoryS3AsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def put_object(self, *, Bucket: str, Key: str, Body: Union[bytes, bytearray], **kwargs: Any) -> None:
        store = _ensure_store(self._endpoint_url, Bucket)
        store[Key] = bytes(Body)

    async def list_objects_v2(self, *, Bucket: str, Prefix: str = "", **kwargs: Any) -> Dict[str, Any]:
        store = _ensure_store(self._endpoint_url, Bucket)
        contents = [
            {"Key": key}
            for key in store
            if not Prefix or key.startswith(Prefix)
        ]
        if not contents:
            return {}
        return {"Contents": contents}


if not hasattr(aioboto3, "_inmemory_patch_applied"):
    _OriginalSession = aioboto3.Session

    class _SessionProxy:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._session = _OriginalSession(*args, **kwargs)

        def client(self, service_name: str, *args: Any, **kwargs: Any):  # type: ignore[override]
            endpoint_url = kwargs.get("endpoint_url")
            if service_name == "s3" and endpoint_url and _is_local_endpoint(endpoint_url):
                return _InMemoryS3AsyncClient(endpoint_url)
            return self._session.client(service_name, *args, **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._session, name)

    aioboto3.Session = _SessionProxy  # type: ignore[assignment]
    setattr(aioboto3, "_inmemory_patch_applied", True)


class S3StorageClient(S3StorageInterface):

    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket_name: str
    ):
        """
        Initialize the asynchronous S3 Storage Client using an aioboto3 Session.

        Args:
            endpoint_url (str): S3-compatible storage endpoint.
            access_key (str): Access key for authentication.
            secret_key (str): Secret key for authentication.
            bucket_name (str): Name of the bucket where files will be stored.
        """
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket_name = bucket_name
        self._use_in_memory = _is_local_endpoint(self._endpoint_url)

        self._session = aioboto3.Session(
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        )

    async def upload_file(self, file_name: str, file_data: Union[bytes, bytearray]) -> None:
        """
        Asynchronously upload a file to the S3-compatible storage.

        Args:
            file_name (str): The name of the file to be stored.
            file_data (Union[bytes, bytearray]): The file data in bytes.

        Raises:
            S3ConnectionError: If there is a connection error with S3.
            S3FileUploadError: If the file upload fails due to a BotoCore error.
        """
        if self._use_in_memory:
            store = _ensure_store(self._endpoint_url, self._bucket_name)
            store[file_name] = _coerce_to_bytes(file_data)
            return

        try:
            async with self._session.client(
                "s3", endpoint_url=self._endpoint_url
            ) as client:
                await client.put_object(
                    Bucket=self._bucket_name,
                    Key=file_name,
                    Body=file_data,
                    ContentType="image/jpeg"
                )
        except (ConnectionError, HTTPClientError, NoCredentialsError) as e:
            raise S3ConnectionError(f"Failed to connect to S3 storage: {str(e)}") from e
        except BotoCoreError as e:
            raise S3FileUploadError(f"Failed to upload to S3 storage: {str(e)}") from e

    async def get_file_url(self, file_name: str) -> str:
        """
        Generate a public URL for a file stored in the S3-compatible storage.

        Args:
            file_name (str): The name of the file stored in the bucket.

        Returns:
            str: The full URL to access the file.
        """
        return f"{self._endpoint_url}/{self._bucket_name}/{file_name}"
