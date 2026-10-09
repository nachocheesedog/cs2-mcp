"""Minimal binary FBX reader: print UnitScaleFactor and the raw Vertices extent (no unit conversion)."""
import struct, sys, zlib
data = open(sys.argv[1], "rb").read()
ver = struct.unpack("<I", data[23:27])[0]
wide = ver >= 7500
def read_node(off):
    if wide: end, nprops, plen = struct.unpack("<QQQ", data[off:off + 24]); off += 24
    else: end, nprops, plen = struct.unpack("<III", data[off:off + 12]); off += 12
    nlen = data[off]; name = data[off + 1:off + 1 + nlen].decode(errors="replace"); off += 1 + nlen
    if end == 0: return None, off
    props = []; p = off
    for _ in range(nprops):
        t = chr(data[p]); p += 1
        if t in "YCIFDL":
            fmt, sz = {"Y": ("<h", 2), "C": ("?", 1), "I": ("<i", 4), "F": ("<f", 4), "D": ("<d", 8), "L": ("<q", 8)}[t]
            props.append(struct.unpack(fmt, data[p:p + sz])[0]); p += sz
        elif t in "fdlib":
            n, enc, clen = struct.unpack("<III", data[p:p + 12]); p += 12
            raw = data[p:p + clen]; p += clen
            if enc: raw = zlib.decompress(raw)
            fmt = {"f": "f", "d": "d", "l": "q", "i": "i", "b": "?"}[t]
            props.append(list(struct.unpack("<%d%s" % (n, fmt), raw)))
        elif t in "SR":
            n = struct.unpack("<I", data[p:p + 4])[0]; p += 4
            props.append(data[p:p + n]); p += n
    children = []; c = off + plen
    while c < end:
        ch, c2 = read_node(c)
        if ch is None: break
        children.append(ch); c = c2
    return (name, props, children), end
nodes = []; off = 27
while off < len(data) - 200:
    n, off2 = read_node(off)
    if n is None: break
    nodes.append(n); off = off2
def walk(ns):
    for n in ns:
        yield n
        yield from walk(n[2])
for name, props, ch in walk(nodes):
    if name == "P" and props and props[0] in (b"UnitScaleFactor", b"OriginalUnitScaleFactor", b"UpAxis", b"FrontAxis"):
        print(props[0].decode(), props[-1])
    if name == "Vertices":
        v = props[0]; xs, ys, zs = v[0::3], v[1::3], v[2::3]
        print("raw vertex extent x %.3f..%.3f  y %.3f..%.3f  z %.3f..%.3f" % (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)))
    if name == "Lcl Scaling" or (name == "P" and props and props[0] == b"Lcl Scaling"):
        print("Lcl Scaling", props[-3:])
    if name == "P" and props and props[0] in (b"Lcl Rotation",):
        print("Lcl Rotation", props[-3:])
