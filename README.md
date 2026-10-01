# LAN HTTP file server (SE_04 network programming)

A **small educational HTTP/1.1 subset** implemented using Python's TCP sockets, not `http.server` or a web framework. Serves files from one directory. Designed for a live code walkthrough, **not production or untrusted networks**.

## Run

Requires Python 3.10+ on a POSIX system with `O_DIRECTORY` and `O_NOFOLLOW` (tested on Linux); no Python dependencies or installation needed.

```sh
python3 -m lan_http --root ./shared --port 8080
curl -i http://127.0.0.1:8080/hello.txt
curl -I http://127.0.0.1:8080/hello.txt
```

Create `shared/hello.txt` first (or use any existing directory and filename). The default host `127.0.0.1` is accessible only locally. For another device on a **trusted** LAN, use `--host 0.0.0.0`, then connect to this machine's LAN IP. Stop with Ctrl-C. Never expose this teaching server to the public internet.

```sh
python3 -m unittest discover -s tests -v
```

## What to show at the oral assessment

1. Start the server and request a binary or text file with `curl -v` and `curl -I`. Identify request line, headers, blank line, status, `Content-Length`, and body.
2. Open `lan_http/server.py`: TCP listener, `accept`, per-client thread, repeated `recv` until CRLF-CRLF, request validation, descriptor-relative path traversal, response framing, file chunks via `sendall`.
3. Demonstrate `404`, `405` (`curl -X POST -i ...`), and an incomplete or oversized request using the tests. Explain the `408` timeout and why a stalled client must not block everyone else.
4. Run the test suite. Show the traversal and concurrent-client tests. Explain why descriptor-relative opening matters, but also why this is **not** a hardened public server.

## Network design decisions

- HTTP/1.1 is an application protocol over TCP. TCP guarantees ordered, reliable **bytes**, not message boundaries: one `recv` may contain only part of the headers or more than one packet. We accumulate until the header terminator, with a 16 KiB header limit.
- One connection handles **one request**, then closes (`Connection: close`). This deliberately avoids persistent-connection and request-body framing complexity. `GET` and `HEAD` need no request body here.
- File data is sent in 64 KiB chunks, avoiding a whole-file memory load. `Content-Length` is the file size; `HEAD` sends identical headers but no body.
- A thread per connection allows a slow client and another client to coexist. A two-second socket timeout bounds inactivity, but **threads are not capped**, so this is unsuitable for hostile clients.
- Paths are opened relative to a held root directory descriptor with `O_NOFOLLOW` on each component; symlinks in requested paths are not followed. `fstat` determines the opened file's size. A timeout after response headers start closes the connection without adding a second response. Files changed while serving are still not snapshotted.

## Supported / unsupported

Supports origin-form `GET` and `HEAD` for regular files, basic `Host` validation, MIME type guessing, `200`, `400`, `403`, `404`, `405`, `408`, `413`, and query strings on paths. Always closes connections.

Does **not** implement full HTTP/1.1 compliance, request bodies, keep-alive, ranges/resumable downloads, uploads, directory listings, TLS, auth, IPv6, structured logs, bounded worker pools, all header syntax rules, or an HTTP security audit. MIME is inferred from the filename and is untrusted. Use a maintained web server for real deployment.

## Next exercises (not part of MVP)

- Add `Range: bytes=...` and `206 Partial Content` to resume large downloads.
- Bound simultaneous clients and return `503` under load.
- Measure throughput and compare thread-per-client with selectors/async I/O.
- Put a production reverse proxy in front, and compare HTTPS/TLS and HTTP/2 behavior.
