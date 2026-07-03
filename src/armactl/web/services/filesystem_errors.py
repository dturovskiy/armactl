"""Controlled errors for the web file browser."""

from __future__ import annotations


class FileBrowserError(RuntimeError):
    """Raised when the web file browser rejects or cannot load a request."""

    public_message = "Unsafe file path."
    status_code = 400

    def __init__(self, message: str | None = None) -> None:
        public_message = message or self.public_message
        super().__init__(public_message)
        self.public_message = public_message


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


class ReplacementUnavailableError(FileBrowserError):
    """Raised when a file replacement is not allowed for a safe location."""

    public_message = "File replacement unavailable."
    status_code = 400


class ReplacementTooLargeError(FileBrowserError):
    """Raised when a replacement upload exceeds the configured streaming limit."""

    public_message = "Replacement file too large."
    status_code = 413


class ReplacementInvalidContentError(FileBrowserError):
    """Raised when replacement bytes are not valid for the target file."""

    public_message = "Replacement file is not valid for this target."
    status_code = 400
