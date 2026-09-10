"""Optional MLflow file-store adapter, loaded only through dmux.adapters."""

from .adapter import MLflowAdapter

__all__ = ["MLflowAdapter"]
