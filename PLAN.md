# Assessment project plan

**Goal:** Present an understandable, tested TCP-socket HTTP/1.1 file server in a 15–30 minute oral assessment. This plan is based on the module overview and learning resources supplied by Markus; the detailed module requirements/rubric were not provided.

1. **Vertical slice:** GET one file via TCP; verify status, length, and body with a real socket test. Done.
2. **Protocol and errors:** HEAD; request framing/validation; explicit 400/404/405/413; content type. Done.
3. **Reliability and security:** request timeout/408, path traversal checks, concurrent clients, binary chunk streaming. Done.
4. **Presentation:** document boundaries, run the full suite, exercise CLI with curl, and rehearse a 5-minute code tour. Automated/CLI verification required before claiming completion.

**Success criteria:** all integration tests pass; curl downloads the correct bytes and reports headers; malformed requests receive expected statuses; slow clients do not block a healthy one; the presentation clearly distinguishes TCP from HTTP and identifies unsupported features.

**Explicit exclusions:** HTTP/2, TLS, uploads, persistence/keep-alive, authentication, full RFC conformance, deployment on the internet. Optional extension after the MVP: byte-range downloads.

**Assessment talking points:** Why TCP instead of UDP? How are HTTP messages delimited? What if `recv()` returns half a header? What happens when a client hangs or disconnects? How is `Content-Length` determined without loading a whole file? What limits protect resources? Where is this server still insecure? What would change for production?
