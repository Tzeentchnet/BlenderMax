# File structure and chunks

## Status

The OLE container and chunk framing are **confirmed** for the variants handled
by BlenderMax. The semantic meaning of many class-specific payloads remains
version-dependent or unknown.

## OLE Compound File container

A native `.max` file begins with the standard Compound File Binary signature:

```text
D0 CF 11 E0 A1 B1 1A E1
```

BlenderMax reads the normal CFB header, FAT, DIFAT, MiniFAT, directory, and
UTF-16LE directory-entry names. Relevant implementation details are in
`ImportMaxFile` in [`source/import_max.py`](../source/import_max.py).

Observed container properties:

- The CFB header is 512 bytes.
- Sector size is `2 ** sector_shift`; common CFB variants use 512-byte or
  4096-byte sectors.
- Mini sectors use `2 ** mini_sector_shift`.
- Streams smaller than the CFB cutoff, normally 4096 bytes, use the MiniFAT.
- Directory entries are 128 bytes and store UTF-16LE names.
- Stream lookup is case-insensitive.
- A missing named stream must be treated as missing. Falling through to a
  neighboring directory entry can make unrelated bytes appear to parse
  successfully.

The field named `dll_version` in the current parser comes from the CFB header's
major-version position. It describes the compound-file implementation, not
the 3ds Max product release. Researchers should not use it as a MAX-version
identifier.

## Common named streams

The following streams have been observed and are used by the importer:

| Stream | Purpose | Status |
|---|---|---|
| `ClassData` | Additional serialized class data | Tentative |
| `ClassDirectory3` | Newer per-file class table variant | Confirmed |
| `ClassDirectory2` | Legacy per-file class table variant | Confirmed |
| `ClassDirectory` | Older unnumbered class table variant | Confirmed |
| `Config` | Scene timing and other global configuration | Confirmed in part |
| `DllDirectory` | Plug-in/DLL names referenced by class entries | Confirmed in part |
| `Scene` | Serialized object graph | Confirmed in part |
| `VideoPostQueue` | Video-post configuration | Parsed, semantics mostly unknown |

Summary-information and other metadata streams may also be present. Their
presence and directory order are not stable and should not be used to infer a
3ds Max release.

Exactly one class-directory variant may be present. BlenderMax probes
`ClassDirectory3`, then `ClassDirectory2`, then `ClassDirectory`. A parser
must distinguish "stream absent" from "stream exists but is malformed"; a
malformed preferred stream should not silently fall back to another name.

## Chunk framing

MAX streams contain nested chunks. The observed base header is:

```text
offset  size  type     meaning
0       2     uint16   chunk type
2       4     int32    signed total size and container flag
```

The size includes the chunk header. Its sign bit acts as a container marker:

- Positive size: primitive payload.
- Negative size: container whose payload is another chunk sequence.
- Zero 32-bit size: an additional signed 64-bit size follows the six-byte
  header; its sign bit has the same container meaning.

Equivalent decoding logic:

```text
type, signed_size = read("<Hi")
header_size = 6

if signed_size == 0:
    signed_size = read("<q")
    header_size += 8
    total_size = signed_size & 0x7FFF_FFFF_FFFF_FFFF
else:
    total_size = signed_size & 0x7FFF_FFFF

payload = bytes[header_size:total_size]
is_container = signed_size < 0
```

The next sibling begins at `current_offset + total_size`.

### Stream envelope and compression

At stream depth zero, the current reader recognizes a leading root value
`0x8B1F` and several values associated with compressed variants. It attempts
zlib/gzip-compatible decompression for those cases. This path is
**tentative**: researchers should preserve the original prefix, compressed
length, decompressed length, and exact failing sample characteristics when
adding another compression variant.

## Primitive chunk decoding

Some chunk types have stable primitive interpretations across the contexts
currently supported:

| Chunk types | Current interpretation |
|---|---|
| `0x0340`, `0x0456`, `0x0962`, `0x10A0`, `0x1010`, `0x1230`, `0x4001` | UTF-16LE string |
| `0x1020`, `0x1030`, `0x1050`, `0x1080`, `0x3002`, `0x4003` | One little-endian `uint32` link/value |
| `0x0009`, `0x1040`, `0x2034`, `0x2035` | Sequence of little-endian `uint32` values |
| `0x0103`, `0x0105`, `0x099C`, `0x2500`, `0x3005` | Sequence of little-endian `uint16` values |
| `0x0102`, `0x096A`, `0x096B`, `0x096C`, `0x09ED`, `0x2501`, `0x2503`, `0x2504`, `0x2505`, `0x2511` | Sequence of little-endian `float32` values |

These interpretations are contextual, not universal. If a payload length is
incompatible with the expected primitive width, preserve it as bytes rather
than truncating it.

## Class-directory indirection

Scene chunks generally store a small class-table index in their 16-bit chunk
type. `get_class()` resolves that index into the active class directory.

Two class-directory child chunks are currently understood:

| Chunk | Layout | Meaning |
|---|---|---|
| `0x2042` | UTF-16LE string | Human-readable class name |
| `0x2060` | `<I Q I>` | DLL index, combined 64-bit class ID, 32-bit superclass ID |

The combined class ID follows the representation used by BlenderMax:

```text
combined = (Class_ID.PartB << 32) | Class_ID.PartA
```

This allows constants from the 3ds Max SDK to be compared with the 64-bit
values listed in the scene-data documentation.

The DLL index points into `DllDirectory`. DLL names are useful provenance, but
they are not sufficient to decode an object: plug-in versions can serialize
the same class differently.

## Scene references

The `Scene` stream is parsed into one indexed child array. References point
back into this array:

### Positional references: `0x2034`

The payload is a sequence of `uint32` Scene child indices. Order is
class-specific. Missing or invalid entries should remain distinguishable from
index zero.

### Keyed references: `0x2035`

The current decoder treats the first `uint32` as a header value and the
remaining payload as `(key, scene_index)` pairs:

```text
header, key0, index0, key1, index1, ...
```

The exact meaning of the header is **tentative**. The keyed mapping itself is
confirmed for node transform/object/material/layer slots.

## Parser invariants

Researchers extending the decoder should preserve these invariants:

1. Bounds-check every count against the bytes remaining.
2. Reject a chunk whose declared size cannot advance the input offset.
3. Keep unknown payloads as bytes.
4. Resolve class IDs before assigning class-specific meaning to a Scene chunk.
5. Detect reference cycles while traversing object graphs.
6. Treat absent streams, malformed streams, and unsupported classes as three
   different outcomes.
7. Never use a successful OLE parse alone as proof that scene data was
   reconstructed.

## Open questions

- The complete semantics of `ClassData`.
- Version rules for compressed stream envelopes.
- Stable product-version identification independent of the CFB version.
- The meaning of the first `0x2035` value in all class families.
- Whether primitive type assignments differ in unsupported plug-in payloads.
