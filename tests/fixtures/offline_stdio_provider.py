"""Test-only provider injection for the real launcher/stdio contract fixture.

Loaded explicitly by the temporary copied launcher, never by installed runtime.
Only embedding inference is replaced; persistence, indexing, MCP and policies run.
"""
import hashlib
import math

from workspace import server
from workspace.analytics import EventStore


class _Tokenizer:
    model_max_length = 32768

    def __call__(self, text, add_special_tokens=False, **kwargs):
        if isinstance(text, list):
            return {"input_ids": [list(range(len(value))) for value in text]}
        return {"input_ids": list(range(len(text))) + ([999] if add_special_tokens else []),
                "offset_mapping": [(i, i + 1) for i in range(len(text))]}


class _Provider:
    def __init__(self, config):
        self.dimensions = config["profile"]["dimensions"]

    def tokenizer(self):
        return _Tokenizer()

    def embed(self, texts, purpose="document"):
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            vector = [(digest[i % len(digest)] - 127.5) for i in range(self.dimensions)]
            norm = math.sqrt(sum(value * value for value in vector))
            vectors.append([value / norm for value in vector])
        return vectors

    def close(self):
        pass


_runtime = server.runtime


def _offline_runtime(work_root, workspace, provider_factory=None):
    store = EventStore(work_root)
    plan = store.storage_prepare()
    if plan['needs_migration']:
        store.storage_apply(plan['revision'])
    return _runtime(work_root, workspace, provider_factory=provider_factory or _Provider)


server.runtime = _offline_runtime
