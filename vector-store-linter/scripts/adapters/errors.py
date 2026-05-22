"""Shared exception types for vector store adapters.

base.py defines the adapter interface and is intentionally left untouched, so
the exceptions adapters and the CLI share live here instead. The CLI maps
StoreConnectionError to the documented store-connection exit code.
"""

from __future__ import annotations


class StoreConnectionError(Exception):
    """Raised when an adapter cannot connect to or read from a store.

    Covers a missing or invalid credential, an index or table that does not
    exist, a network failure, and an unsupported operation on the target store
    (for example a full read on an index that cannot list its ids). The message
    is intended to be shown to the user.
    """
