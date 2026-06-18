"""Controlled errors for the web file browser."""

from __future__ import annotations


class FileBrowserError(RuntimeError):
    """Raised when the web file browser rejects or cannot load a request."""

    public_message = "Unsafe file path."
    status_code = 400


class UnknownFileRootError(FileBrowserError):
    """Raised when a requested root id is not allowlisted."""

    public_message = "Unknown file root."
    status_code = 404


class RootUnavailableError(FileBrowserError):
    """Raised when a configured root is missing or unsafe."""

    public_message = "Root unavailable."
    status_code = 400


class UnsafeFilePathError(FileBrowserError):
    """Raised when a browser path escapes the selected root."""

    public_message = "Unsafe file path."
    status_code = 400


class PathUnavailableError(FileBrowserError):
    """Raised when a safe relative path does not point at the expected object."""

    public_message = "Path unavailable."
    status_code = 404


class DownloadUnavailableError(FileBrowserError):
    """Raised when a safe path cannot be downloaded as a single file."""

    public_message = "Download unavailable."
    status_code = 400


class UploadUnavailableError(FileBrowserError):
    """Raised when an upload cannot be accepted for a safe location."""

    public_message = "Upload unavailable."
    status_code = 400


class UploadTooLargeError(FileBrowserError):
    """Raised when an upload exceeds the configured streaming limit."""

    public_message = "Upload too large."
    status_code = 413


class InvalidUploadFilenameError(FileBrowserError):
    """Raised when an upload filename is empty or unsafe."""

    public_message = "Invalid filename."
    status_code = 400


class UploadTargetExistsError(FileBrowserError):
    """Raised when an upload would overwrite an existing file."""

    public_message = "File already exists."
    status_code = 409
