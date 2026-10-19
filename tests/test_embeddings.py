from __future__ import annotations

import copy
import io
import json
import math
import os
import sys
import unittest
import weakref
from http.client import BadStatusLine
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib import error, request


PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "persistent-context-workspace"
sys.path.insert(0, str(PLUGIN))
from pcw_runtime import embeddings as e


def small_config(kind="sentence-transformers"):
    cfg = e.default_config()
    cfg["profile"]["dimensions"] = 2
    if kind != "sentence-transformers":
        cfg["provider"] = {"type": kind, "base_url": "http://localhost:11434"}
    return cfg


def raw_config(kind="openai-compatible"):
    cfg = small_config(kind)
    cfg["profile"]["input_format"] = "raw"
    cfg["profile"].pop("query_instruction")
    return cfg


class Tokenizer:
    model_max_length = 32768

    def __init__(self, count=None):
        self.count = count
        self.calls = []

    def __call__(self, texts, **kwargs):
        self.calls.append((list(texts), kwargs))
        # Include one special token, making boundary tests meaningful.
        return {"input_ids": [[1] * (self.count if self.count is not None else len(text) + 1) for text in texts]}


class Model:
    max_seq_length = 32768

    def __init__(self, count=None):
        self.tokenizer = Tokenizer(count)
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), kwargs))
        return [[3.0, 4.0] for text in texts]


class ConfigTests(unittest.TestCase):
    def test_default_is_local_pinned_1024_and_independent(self):
        cfg = e.default_config()
        self.assertEqual(cfg["provider"]["type"], "sentence-transformers")
        self.assertEqual(cfg["profile"]["model"], "Qwen/Qwen3-Embedding-0.6B")
        self.assertRegex(cfg["profile"]["revision"], r"^[0-9a-f]{40}$")
        self.assertEqual(cfg["profile"]["dimensions"], 1024)
        cfg["profile"]["dimensions"] = 2
        self.assertEqual(e.default_config()["profile"]["dimensions"], 1024)

    def test_identity_tracks_vector_space_and_source(self):
        cfg = small_config()
        original = e.embedding_identity(cfg)
        self.assertEqual(original, e.embedding_identity(copy.deepcopy(cfg)))
        for key, value in {
            "model": "other/model", "revision": "other-revision", "dimensions": 4,
            "normalization": "none", "query_instruction": "Find relevant source text.",
            "max_input_tokens": 1024, "precision": "float16",
        }.items():
            with self.subTest(key=key):
                changed = copy.deepcopy(cfg)
                changed["profile"][key] = value
                self.assertNotEqual(original, e.embedding_identity(changed))
        changed = copy.deepcopy(cfg)
        changed["provider"]["device"] = "cuda"
        self.assertNotEqual(original, e.embedding_identity(changed))
        changed = small_config("ollama")
        self.assertNotEqual(original, e.embedding_identity(changed))

    def test_credential_rotation_and_cache_change_preserve_identity(self):
        cfg = small_config("openai-compatible")
        original = e.embedding_identity(cfg)
        cfg["provider"].update(api_key_env="PCW_DIFFERENT_KEY", timeout_seconds=120)
        with patch.dict(os.environ, {"PCW_DIFFERENT_KEY": "not-a-real-key"}):
            self.assertEqual(original, e.embedding_identity(cfg))
        cfg["provider"]["base_url"] = "https://different.example"
        self.assertNotEqual(original, e.embedding_identity(cfg))
        cfg = small_config()
        original = e.embedding_identity(cfg)
        cfg["provider"].update(cache_folder="/tmp/model-cache", local_files_only=True)
        self.assertEqual(original, e.embedding_identity(cfg))

    def test_normalized_connection_addresses_have_same_identity(self):
        cfg = small_config("openai-compatible")
        expected = e.embedding_identity(cfg)
        for url in ("http://localhost:11434/", "http://localhost:11434/v1", "http://localhost:11434/v1/embeddings"):
            cfg["provider"]["base_url"] = url
            self.assertEqual(expected, e.embedding_identity(cfg))

    def test_rejects_unsupported_providers_and_inline_credentials(self):
        for bad in ("luna", "chat", "fake", None, {}):
            cfg = small_config()
            cfg["provider"]["type"] = bad
            with self.subTest(provider=bad), self.assertRaises(e.EmbeddingError):
                e.create_provider(cfg)
        for key in ("api_key", "headers", "token", "password"):
            cfg = small_config("openai-compatible")
            cfg["provider"][key] = "private-value"
            with self.subTest(key=key), self.assertRaises(e.EmbeddingError) as context:
                e.validate_config(cfg)
            self.assertNotIn("private-value", str(context.exception))
        for url in (
            "https://user:private-value@example.com", "https://example.com?key=private-value",
            "https://example.com#private-value", "file:///tmp/model", "https://example.com:bad",
        ):
            cfg = small_config("ollama")
            cfg["provider"]["base_url"] = url
            with self.subTest(url=url), self.assertRaises(e.EmbeddingError) as context:
                e.validate_config(cfg)
            self.assertNotIn("private-value", str(context.exception))

    def test_profile_settings_must_be_explicit_and_valid(self):
        cases = [("dimensions", 0), ("dimensions", True), ("normalization", "auto"),
                 ("max_input_tokens", -1), ("input_format", "unknown"), ("revision", ""),
                 ("query_instruction", "two\nlines")]
        for key, value in cases:
            cfg = small_config()
            cfg["profile"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(e.EmbeddingError):
                e.validate_config(cfg)
        cfg = small_config()
        cfg["profile"]["input_format"] = "raw"
        with self.assertRaises(e.EmbeddingError):
            e.validate_config(cfg)


class LocalTests(unittest.TestCase):
    def test_lazy_load_reuse_exact_query_prefix_and_document_bytes(self):
        model = Model()
        with patch.object(e, "_load_sentence_transformer", return_value=model) as factory:
            provider = e.create_provider(small_config())
            factory.assert_not_called()
            self.assertEqual(provider.embed([], "document"), [])
            factory.assert_not_called()
            document = "  исходный текст\n| table |\n```code```\n"
            self.assertEqual(provider.embed([document]), [[0.6, 0.8]])
            prefix = f"Instruct: {e.QUERY_INSTRUCTION}\nQuery: "
            provider.embed(["вопрос", prefix + "уже оформлен"], "query")
            factory.assert_called_once()
            self.assertEqual(model.calls[0][0], [document])
            self.assertEqual(model.calls[1][0], [prefix + "вопрос", prefix + "уже оформлен"])
            for _, options in model.calls:
                self.assertEqual(options["prompt"], "")
                self.assertNotIn("prompt_name", options)
                self.assertFalse(options["show_progress_bar"])
                self.assertEqual(options["batch_size"], 1)
                self.assertEqual(options["precision"], "float32")
            self.assertEqual(factory.call_args.kwargs["revision"], e.QWEN_REVISION)
            self.assertFalse(factory.call_args.kwargs["trust_remote_code"])
            self.assertEqual(model.tokenizer.calls[0][1]["truncation"], False)
            self.assertTrue(model.tokenizer.calls[0][1]["add_special_tokens"])

    def test_cached_weights_options_forwarded_without_fallback(self):
        cfg = small_config()
        cfg["provider"].update(cache_folder="/tmp/pcw-test-cache", local_files_only=True)
        with patch.object(e, "_load_sentence_transformer", side_effect=RuntimeError("private dependency details")) as factory:
            provider = e.create_provider(cfg)
            with self.assertRaises(e.EmbeddingError) as context:
                provider.embed(["doc"])
            self.assertNotIn("private dependency details", str(context.exception))
            self.assertEqual(factory.call_args.kwargs["cache_folder"], "/tmp/pcw-test-cache")
            self.assertTrue(factory.call_args.kwargs["local_files_only"])
            self.assertIsNone(provider._model)

    def test_missing_optional_dependency_is_an_actionable_error(self):
        with patch.dict(sys.modules, {"sentence_transformers": None}):
            with self.assertRaisesRegex(e.EmbeddingError, "require sentence-transformers"):
                e.create_provider(small_config()).embed(["doc"])

    def test_rejects_overflow_before_encode_including_special_tokens(self):
        model = Model(count=9)
        cfg = small_config()
        cfg["profile"]["max_input_tokens"] = 8
        with patch.object(e, "_load_sentence_transformer", return_value=model):
            with self.assertRaisesRegex(e.EmbeddingError, "9 tokens; limit is 8"):
                e.create_provider(cfg).embed(["some input"])
        self.assertEqual(model.calls, [])

    def test_effective_limit_uses_model_and_tokenizer_limits(self):
        for field in ("model", "tokenizer"):
            with self.subTest(field=field):
                model = Model(count=11)
                if field == "model":
                    model.max_seq_length = 10
                else:
                    model.tokenizer.model_max_length = 10
                with patch.object(e, "_load_sentence_transformer", return_value=model):
                    with self.assertRaisesRegex(e.EmbeddingError, "limit is 10"):
                        e.create_provider(small_config()).embed(["doc"])
                self.assertFalse(model.calls)

    def test_exact_token_limit_is_accepted(self):
        model = Model(count=10)
        model.max_seq_length = 10
        with patch.object(e, "_load_sentence_transformer", return_value=model):
            self.assertEqual(e.create_provider(small_config()).embed(["doc"]), [[0.6, 0.8]])

    def test_unknown_token_limits_fail_without_encode(self):
        model = Model()
        model.tokenizer = None
        with patch.object(e, "_load_sentence_transformer", return_value=model):
            with self.assertRaisesRegex(e.EmbeddingError, "verify local embedding token limits"):
                e.create_provider(small_config()).embed(["doc"])
        self.assertFalse(model.calls)

    def test_close_releases_model_and_cannot_reload(self):
        refs = []

        def factory(*args, **kwargs):
            model = Model()
            refs.append(weakref.ref(model))
            return model

        with patch.object(e, "_load_sentence_transformer", side_effect=factory) as loader:
            provider = e.create_provider(small_config())
            provider.embed(["doc"])
            self.assertIsNotNone(refs[0]())
            provider.close()
            provider.close()
            self.assertIsNone(refs[0]())
            with self.assertRaisesRegex(e.EmbeddingError, "closed"):
                provider.embed(["doc"])
            loader.assert_called_once()

    def test_inputs_are_validated_before_model_loading(self):
        with patch.object(e, "_load_sentence_transformer") as loader:
            provider = e.create_provider(small_config())
            for texts, purpose in (("text", "document"), ([""], "document"), ([1], "query"), (["text"], "chat")):
                with self.subTest(texts=texts, purpose=purpose), self.assertRaises(e.EmbeddingError):
                    provider.embed(texts, purpose)
            loader.assert_not_called()

    def test_accelerator_cache_is_released_after_model_reference(self):
        cfg = small_config()
        cfg["provider"]["device"] = "cuda:0"
        cuda = SimpleNamespace(empty_cache=Mock())
        with patch.object(e, "_load_sentence_transformer", return_value=Model()), \
                patch.dict(sys.modules, {"torch": SimpleNamespace(cuda=cuda)}):
            provider = e.create_provider(cfg)
            provider.embed(["doc"])
            provider.close()
            self.assertIsNone(provider._model)
            cuda.empty_cache.assert_called_once()


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.opener = Mock()
        self.patch = patch.object(e.request, "build_opener", return_value=self.opener)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def response(self, body):
        self.opener.open.return_value = io.BytesIO(json.dumps(body).encode())

    def sent(self):
        req = self.opener.open.call_args.args[0]
        return req, json.loads(req.data)

    def test_ollama_full_documents_and_explicit_no_truncation(self):
        self.response({"embeddings": [[3, 4], [4, 3]]})
        documents = ["a\n\nb", " исходный текст "]
        provider = e.create_provider(small_config("ollama"))
        self.opener.open.assert_not_called()
        self.assertEqual(provider.embed(documents), [[0.6, 0.8], [0.8, 0.6]])
        req, body = self.sent()
        self.assertEqual(req.full_url, "http://localhost:11434/api/embed")
        self.assertEqual(body["input"], documents)
        self.assertIs(body["truncate"], False)
        self.assertEqual(body["model"], e.QWEN_MODEL)
        self.assertNotIn("Authorization", req.headers)
        self.assertNotIn("dimensions", body)

    def test_compatible_api_restores_original_order_and_reads_env_key(self):
        self.response({"data": [{"index": 1, "embedding": [4, 3]}, {"index": 0, "embedding": [3, 4]}]})
        cfg = raw_config()
        cfg["provider"].update(base_url="https://embeddings.example/v1", api_key_env="PCW_TEST_EMBED_KEY")
        with patch.dict(os.environ, {"PCW_TEST_EMBED_KEY": "not-a-real-api-key"}):
            provider = e.create_provider(cfg)
            vectors = provider.embed(["first", "second"], "query")
        self.assertEqual(vectors, [[0.6, 0.8], [0.8, 0.6]])
        req, body = self.sent()
        self.assertEqual(req.full_url, "https://embeddings.example/v1/embeddings")
        self.assertEqual(body["input"], ["first", "second"])
        self.assertEqual(body["encoding_format"], "float")
        self.assertNotIn("truncate", body)
        self.assertEqual(req.get_header("Authorization"), "Bearer not-a-real-api-key")
        self.assertNotIn("not-a-real-api-key", json.dumps(provider.config))

    def test_explicit_dimensions_and_custom_prefix_format(self):
        cfg = raw_config()
        cfg["provider"]["send_dimensions"] = True
        cfg["profile"].update(input_format="prefix", query_prefix="query: ", document_prefix="passage: ")
        provider = e.create_provider(cfg)
        for purpose, expected in (("document", "passage: text"), ("query", "query: text")):
            self.response({"data": [{"index": 0, "embedding": [3, 4]}]})
            provider.embed(["text"], purpose)
            _, body = self.sent()
            self.assertEqual(body["input"], [expected])
            self.assertEqual(body["dimensions"], 2)

    def test_invalid_indices_are_rejected(self):
        entries = [
            [{"index": 0, "embedding": [1, 2]}, {"index": 0, "embedding": [1, 2]}],
            [{"index": -1, "embedding": [1, 2]}, {"index": 1, "embedding": [1, 2]}],
            [{"index": True, "embedding": [1, 2]}, {"index": 1, "embedding": [1, 2]}],
            [{"index": 2, "embedding": [1, 2]}, {"index": 1, "embedding": [1, 2]}],
            [{"embedding": [1, 2]}, {"index": 1, "embedding": [1, 2]}],
            [{"index": 0, "embedding": None}, {"index": 1, "embedding": [1, 2]}],
        ]
        provider = e.create_provider(raw_config())
        for data in entries:
            with self.subTest(data=data):
                self.response({"data": data})
                with self.assertRaises(e.EmbeddingError):
                    provider.embed(["a", "b"])

    def test_bad_response_count_shape_and_values_are_rejected(self):
        provider = e.create_provider(small_config("ollama"))
        for vectors in (None, [], [[1]], [[0, 0]], [[float("nan"), 1]],
                        [[float("inf"), 1]], [[True, 1]], [["1", 2]], [[1, 2], [2, 3]]):
            with self.subTest(vectors=vectors):
                self.response({"embeddings": vectors})
                with self.assertRaises(e.EmbeddingError):
                    provider.embed(["document"])

    def test_remote_errors_never_expose_key_body_or_url(self):
        secret = "private-credential-or-document"
        provider = e.create_provider(raw_config())
        for exc in (
            error.HTTPError("https://example.com/" + secret, 413, secret, {}, io.BytesIO(secret.encode())),
            error.URLError(secret), OSError(secret), BadStatusLine(secret),
        ):
            with self.subTest(exc=type(exc).__name__):
                self.opener.open.side_effect = exc
                with self.assertRaises(e.EmbeddingError) as context:
                    provider.embed(["doc"])
                self.assertNotIn(secret, str(context.exception))
                self.assertTrue(context.exception.__suppress_context__)
        self.opener.open.side_effect = None
        for payload in ({"error": secret}, [secret], {"data": [{"index": 0}]}):
            self.response(payload)
            with self.assertRaises(e.EmbeddingError) as context:
                provider.embed(["doc"])
            self.assertNotIn(secret, str(context.exception))

    def test_missing_or_malformed_secret_fails_without_network(self):
        cfg = raw_config()
        cfg["provider"]["api_key_env"] = "PCW_TEST_MISSING_EMBED_KEY"
        provider = e.create_provider(cfg)
        for value in ("", "header\rinjection", "header\ninjection", "non-ascii-я"):
            with patch.dict(os.environ, {"PCW_TEST_MISSING_EMBED_KEY": value}):
                with self.assertRaises(e.EmbeddingError):
                    provider.embed(["doc"])
        self.opener.open.assert_not_called()

    def test_redirects_are_refused_and_close_is_idempotent(self):
        req = request.Request("https://source.example", headers={"Authorization": "Bearer private"})
        self.assertIsNone(e._NoRedirect().redirect_request(req, None, 302, "redirect", {}, "https://other.example"))
        provider = e.create_provider(raw_config())
        provider.close()
        provider.close()
        self.opener.close.assert_called_once()
        with self.assertRaisesRegex(e.EmbeddingError, "closed"):
            provider.embed(["doc"])

    def test_normalization_none_preserves_numbers_and_l2_handles_extremes(self):
        cfg = small_config("ollama")
        cfg["profile"]["normalization"] = "none"
        self.response({"embeddings": [[3, 4]]})
        self.assertEqual(e.create_provider(cfg).embed(["doc"]), [[3.0, 4.0]])
        provider = e.create_provider(small_config("ollama"))
        for vector in ([1e300, 1e300], [1e-300, 1e-300]):
            self.response({"embeddings": [vector]})
            output = provider.embed(["doc"])[0]
            self.assertTrue(all(math.isfinite(value) for value in output))
            self.assertAlmostEqual(sum(value * value for value in output), 1)


if __name__ == "__main__":
    unittest.main()
