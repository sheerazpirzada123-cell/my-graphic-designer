import base64
import re
import zlib


def pdf_content(path) -> bytes:
    """All decoded content streams of a ReportLab PDF (ASCII85 + Flate)."""
    data = open(path, "rb").read()
    out = b""
    for s in re.findall(rb"stream\r?\n(.*?)endstream", data, re.S):
        s = s.strip()
        if s.endswith(b"~>"):
            s = s[:-2]
        try:
            out += zlib.decompress(base64.a85decode(s)) + b"\n"
        except Exception:
            pass
    return out


def media_box(path) -> tuple[float, float, float, float]:
    data = open(path, "rb").read()
    m = re.search(rb"/MediaBox \[ ?([\d. -]+) ?\]", data)
    return tuple(float(x) for x in m.group(1).split())


def colour_ops(path) -> set[str]:
    txt = pdf_content(path)
    return {m.decode() for m in re.findall(rb"(?<![A-Za-z/])(rg|RG|g|G|k|K)(?![A-Za-z])", txt)}
