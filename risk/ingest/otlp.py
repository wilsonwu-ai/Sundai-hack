"""Decode OTLP/HTTP trace exports (protobuf or JSON) into flat span dicts."""

import base64
import gzip
import json
from collections.abc import Iterator
from typing import Any

from google.protobuf import json_format
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue

PROTOBUF = "application/x-protobuf"
JSON = "application/json"

# OTLP/JSON writes these ids as hex; proto3 JSON (what json_format reads) expects base64.
_ID_FIELDS = {"traceId", "spanId", "parentSpanId", "trace_id", "span_id", "parent_span_id"}


class OTLPDecodeError(ValueError):
    pass


def media_type(content_type: str | None) -> str:
    return (content_type or PROTOBUF).split(";")[0].strip().lower()


def decode_request(
    body: bytes, content_type: str | None, content_encoding: str | None = None
) -> ExportTraceServiceRequest:
    req = ExportTraceServiceRequest()
    try:
        if content_encoding and content_encoding.lower() == "gzip":
            body = gzip.decompress(body)
        if media_type(content_type) == JSON:
            data = json.loads(body)
            _hex_ids_to_base64(data)
            json_format.ParseDict(data, req, ignore_unknown_fields=True)
        else:
            req.ParseFromString(body)
    except Exception as exc:  # malformed body of any kind -> 400
        raise OTLPDecodeError(f"could not decode OTLP traces: {exc}") from exc
    return req


def encode_response(
    content_type: str | None, rejected: int = 0, message: str = ""
) -> tuple[bytes, str]:
    """An ExportTraceServiceResponse in the request's encoding; reports rejected spans
    as a partial success, so exporters don't retry spans that will never pass."""
    resp = ExportTraceServiceResponse()
    if rejected:
        resp.partial_success.rejected_spans = rejected
        resp.partial_success.error_message = message
    if media_type(content_type) == JSON:
        return json.dumps(json_format.MessageToDict(resp)).encode(), JSON
    return resp.SerializeToString(), PROTOBUF


def _hex_ids_to_base64(node: Any) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _ID_FIELDS and isinstance(v, str) and v:
                node[k] = base64.b64encode(bytes.fromhex(v)).decode()
            else:
                _hex_ids_to_base64(v)
    elif isinstance(node, list):
        for v in node:
            _hex_ids_to_base64(v)


def any_value(v: AnyValue) -> Any:
    kind = v.WhichOneof("value")
    if kind is None:
        return None
    if kind == "array_value":
        return [any_value(x) for x in v.array_value.values]
    if kind == "kvlist_value":
        return {kv.key: any_value(kv.value) for kv in v.kvlist_value.values}
    if kind == "bytes_value":
        return base64.b64encode(v.bytes_value).decode()
    return getattr(v, kind)


def attributes(kvs: list[KeyValue]) -> dict[str, Any]:
    return {kv.key: any_value(kv.value) for kv in kvs}


def iter_spans(req: ExportTraceServiceRequest) -> Iterator[dict]:
    for rs in req.resource_spans:
        resource = attributes(rs.resource.attributes)
        for ss in rs.scope_spans:
            for span in ss.spans:
                yield {
                    "trace_id": span.trace_id.hex(),
                    "span_id": span.span_id.hex(),
                    "parent_span_id": span.parent_span_id.hex() or None,
                    "name": span.name,
                    "start_ns": span.start_time_unix_nano,
                    "end_ns": span.end_time_unix_nano or None,
                    "status_code": span.status.code,
                    "attributes": attributes(span.attributes),
                    "resource": resource,
                    "scope": ss.scope.name,
                }
