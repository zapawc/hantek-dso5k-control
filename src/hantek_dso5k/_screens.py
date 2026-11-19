from hashlib import sha256
import json
from ._rgb565 import render

WIDTH, HEIGHT, STRIDE = 800, 480, 1600
REGIONS = {
    "channel_1": ("configuration_text", (14, 449, 175, 480)),
    "channel_2": ("configuration_text", (177, 449, 339, 480)),
    "trigger_source_and_level": ("configuration_text", (344, 449, 510, 480)),
    "timebase": ("configuration_text", (544, 0, 652, 27)),
    "trigger_status": ("runtime_indicator", (264, 0, 375, 27)),
    "acquisition_overview": ("runtime_indicator", (403, 0, 516, 27)),
    "frequency_readout": ("runtime_readout_unattributed", (512, 448, 650, 480)),
    "menu_title": ("context_text", (656, 0, 800, 28)),
    "menu_labels": ("context_text", (656, 29, 800, 480)),
    "waveform_plot": ("moving_plot", (33, 49, 654, 448)),
}


def write_json_new(path, obj):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2)
        handle.write("\n")


def read_raw(source):
    with source.open("rb") as handle:
        data = handle.read(HEIGHT * STRIDE + 1)
    if len(data) != HEIGHT * STRIDE:
        raise ValueError("Expected exactly 768000 graphics bytes")
    return data


def region(data, box):
    x1, y1, x2, y2 = box
    return b"".join(data[y * STRIDE + 2*x1:y * STRIDE + 2*x2] for y in range(y1, y2))


def compare_images(first, second):
    a, b = read_raw(first), read_raw(second)
    output = {}
    for name, (role, box) in REGIONS.items():
        ra, rb = region(a, box), region(b, box)
        changed = sum(ra[i:i+2] != rb[i:i+2] for i in range(0, len(ra), 2))
        output[name] = {"role": role, "box_xyxy_exclusive": box, "pixels": len(ra) // 2,
                        "changed_pixels": changed, "changed_fraction": 2 * changed / len(ra),
                        "first_sha256": sha256(ra).hexdigest(), "second_sha256": sha256(rb).hexdigest()}
    return {"layout": "800x480 RGB565 LE, Utility-menu template",
            "registration": "fixed coordinates, check menu and display format before interpreting differences",
            "coherence_or_freshness": "UNVERIFIED", "regions": output}


def decode(source, preview, labels, *, transport, framing, aggregate, visual):
    data = read_raw(source)
    if framing != "PASS":
        raise ValueError("A preview requires a complete framed graphics stream")
    digest = sha256(data).hexdigest()
    render(source, preview, width=WIDTH, height=HEIGHT, stride=STRIDE, order="little")
    if sha256(read_raw(source)).hexdigest() != digest:
        raise ValueError("Source changed during preview production")
    write_json_new(labels, {
        "source": source.name, "bytes": len(data), "sha256": digest, "preview": preview.name,
        "format": "800x480 RGB565 LE, 1600-byte stride, top-down",
        "transport": transport, "framing": framing, "aggregate": aggregate,
        "visual_integrity": visual, "coherence_or_freshness": "UNVERIFIED",
        "regions": {name: {"role": role, "box_xyxy_exclusive": box} for name, (role, box) in REGIONS.items()},
    })


def review_session(directory):
    session = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    decoded = []
    for step in session["steps"]:
        stage = step["stage"]
        reply = step.get("reply", {})
        if stage.startswith("screen_") and reply.get("graphics_framing") == "PASS":
            data = directory / f"{stage}_payload.bin"
            decode(data, directory / f"{stage}_preview.png", directory / f"{stage}_labels.json",
                   transport="PASS" if step["transport"]["complete"] else "FAIL",
                   framing="PASS", aggregate=reply["aggregate"], visual="UNREVIEWED")
            decoded.append(data)
    if len(decoded) == 2:
        write_json_new(directory / "screen_regions.json", compare_images(*decoded))
