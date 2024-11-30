""" This is where the fuzzing code goes. """

import enum
import itertools
from typing import Final

from http1 import (
    HTTPRequest,
    HTTPResponse,
    remove_request_header,
    translate_request_header_names,
)
from targets import Server
from util import translate

_MIN_GENERATION_SIZE: Final[int] = 10

# These are the requests that the fuzzer starts with
SEEDS: Final[list[list[bytes]]] = [
    [b"GET / HTTP/1.1\r\n\r\n"],
    [b"POST / HTTP/1.1\r\nContent-Length: 10\r\nHost: b\r\n\r\n0123456789"],
    [b"POST / HTTP/1.1\r\nHost: c\r\nTransfer-Encoding: chunked\r\n\r\n5\r\n01234\r\n0\r\n\r\n"],
]


def normalize_request(r1: HTTPRequest, s1: Server, s2: Server) -> HTTPRequest:
    """Normalizes r1 with respect to r2.
    You almost certainly want to call this function twice.
    """
    # If s1 added headers to r1, remove them
    for k in s1.added_headers:
        r1 = remove_request_header(r1, k)

    # If s2 added headers to r2, remove them from r1
    # This ends up being symmetric since this function runs twice.
    for k in (translate(k, s1.header_name_translation) for k in s2.added_headers):
        r1 = remove_request_header(r1, k)

    # If s2 removed or trashed headers from r2, remove them from r1
    # If s1 trashes or (sometimes) removes headers, just remove them
    for k in (
        [translate(k, s1.header_name_translation) for k in s2.removed_headers + s2.trashed_headers]
        + s1.trashed_headers
        + s1.removed_headers
    ):
        r1 = remove_request_header(r1, k)

    # If s2 translates header names, then translate r1's header names.
    if len(s2.header_name_translation) > 0:
        r1 = translate_request_header_names(r1, s2.header_name_translation)

    r1.headers.sort()
    return r1


class DiscrepancyType(enum.Enum):
    NO_DISCREPANCY = 0  # Equal
    STATUS_DISCREPANCY = 1  # Both responses, but different statuses
    SUBTLE_DISCREPANCY = 2  # Both requests, but not equal
    STREAM_DISCREPANCY = 3  # Differing stream length or invalid stream


def categorize_discrepancy(
    parse_trees: list[list[HTTPRequest | HTTPResponse]],
    servers: list[Server],
) -> DiscrepancyType:
    for (pts1, s1), (pts2, s2) in itertools.combinations(zip(parse_trees, servers), 2):
        if s1.doesnt_support_persistence or s2.doesnt_support_persistence:
            pts1 = pts1[:1]
            pts2 = pts2[:1]
        for r1, r2 in itertools.zip_longest(pts1, pts2):
            # If one server responded 400, and the other didn't respond at all, that's okay
            if (r1 is None and isinstance(r2, HTTPResponse) and r2.code == b"400") or (
                r2 is None and isinstance(r1, HTTPResponse) and r1.code == b"400"
            ):
                break

            # One server didn't respond
            if (r1 is None or r2 is None) and r1 is not r2:
                return DiscrepancyType.STREAM_DISCREPANCY

            # One server rejected and the other accepted:
            if (isinstance(r1, HTTPRequest) and not isinstance(r2, HTTPRequest)) or (
                not isinstance(r1, HTTPRequest) and isinstance(r2, HTTPRequest)
            ):
                # If one server parsed a request as HTTP/0.9, and the other doesn't allow 0.9, that's okay.
                if (
                    isinstance(r1, HTTPRequest)
                    and r1.version == b"0.9"
                    and isinstance(r2, HTTPResponse)
                    and not s2.allows_http_0_9
                ) or (
                    isinstance(r2, HTTPRequest)
                    and r2.version == b"0.9"
                    and isinstance(r1, HTTPResponse)
                    and not s2.allows_http_0_9
                ):
                    break
                # If one server requires length in POST requests, and the other doesn't, that's okay.
                if (
                    isinstance(r1, HTTPResponse)
                    and r1.code == b"411"
                    and s1.requires_length_in_post
                    and isinstance(r2, HTTPRequest)
                    and r2.method == b"POST"
                    and not s2.requires_length_in_post
                ) or (
                    isinstance(r2, HTTPResponse)
                    and r2.code == b"411"
                    and s2.requires_length_in_post
                    and isinstance(r1, HTTPRequest)
                    and r1.method == b"POST"
                    and not s1.requires_length_in_post
                ):
                    break
                # If one server requires the host header, and the other doesn't, that's okay.
                if (
                    (r1 is None or (isinstance(r1, HTTPResponse) and r1.code == b"400"))
                    and not s1.allows_missing_host_header
                    and isinstance(r2, HTTPRequest)
                    and s2.allows_missing_host_header
                    and not r2.has_header(b"host")
                ) or (
                    (r2 is None or (isinstance(r2, HTTPResponse) and r2.code == b"400"))
                    and not s2.allows_missing_host_header
                    and isinstance(r1, HTTPRequest)
                    and s1.allows_missing_host_header
                    and not r1.has_header(b"host")
                ):
                    break
                # If one server has a method whitelist, and the request wasn't on it, that's okay.
                if (
                    s1.method_whitelist is not None
                    and isinstance(r1, HTTPResponse)
                    and isinstance(r2, HTTPRequest)
                    and r2.method not in s1.method_whitelist
                ) or (
                    s2.method_whitelist is not None
                    and isinstance(r2, HTTPResponse)
                    and isinstance(r1, HTTPRequest)
                    and r1.method not in s2.method_whitelist
                ):
                    break

                # If one server has a method character blacklist, and the method has a character in the blacklist, that's okay.
                if (
                    isinstance(r1, HTTPResponse)
                    and isinstance(r2, HTTPRequest)
                    and any(b in s1.method_character_blacklist for b in r2.method)
                ) or (
                    isinstance(r2, HTTPResponse)
                    and isinstance(r1, HTTPRequest)
                    and any(b in s2.method_character_blacklist for b in r1.method)
                ):
                    break

                # print(f"{s1.name} rejects when {s2.name} accepts")
                # print(r1)
                # print(r2)
                return DiscrepancyType.STATUS_DISCREPANCY  # True
            # Both servers accepted:
            if isinstance(r1, HTTPRequest) and isinstance(r2, HTTPRequest):
                new_r1: HTTPRequest = normalize_request(r1, s1, s2)
                new_r2: HTTPRequest = normalize_request(r2, s2, s1)
                r1 = new_r1
                r2 = new_r2

                if r1 != r2:
                    # print(f"{s1.name} and {s2.name} accepted with different interpretations.")
                    # print("   ", r1)
                    # print("   ", r2)
                    return DiscrepancyType.SUBTLE_DISCREPANCY
    return DiscrepancyType.NO_DISCREPANCY
