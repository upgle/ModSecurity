#!/usr/bin/env python3
"""Render a dependency-free paired estimation plot from benchmark CSV data."""

import argparse
import csv
import math
import re
import statistics
from pathlib import Path
from xml.etree import ElementTree as ET


SVG_NAMESPACE = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NAMESPACE)

COLORS = {
    "background": "#FFFFFF",
    "foreground": "#17212B",
    "muted": "#596675",
    "grid": "#DCE3E9",
    "pair": "#8793A1",
    "enabled": "#D55E00",
    "disabled": "#0072B2",
    "effect": "#009E73",
}


def svg_element(tag, attributes=None, text=None):
    element = ET.Element("{%s}%s" % (SVG_NAMESPACE, tag), attributes or {})
    if text is not None:
        element.text = text
    return element


def svg_child(parent, tag, attributes=None, text=None):
    element = ET.SubElement(
        parent, "{%s}%s" % (SVG_NAMESPACE, tag), attributes or {})
    if text is not None:
        element.text = text
    return element


def add_text(parent, x, y, value, css_class, anchor=None, transform=None):
    attributes = {
        "x": format_number(x),
        "y": format_number(y),
        "class": css_class,
    }
    if anchor:
        attributes["text-anchor"] = anchor
    if transform:
        attributes["transform"] = transform
    return svg_child(parent, "text", attributes, value)


def add_line(parent, x1, y1, x2, y2, css_class):
    return svg_child(parent, "line", {
        "x1": format_number(x1),
        "y1": format_number(y1),
        "x2": format_number(x2),
        "y2": format_number(y2),
        "class": css_class,
    })


def add_marker(parent, x, y, order, fill, radius=6.0, css_class="point"):
    common = {"class": css_class, "fill": fill}
    if order == "enabled-first":
        common.update({
            "cx": format_number(x),
            "cy": format_number(y),
            "r": format_number(radius),
        })
        return svg_child(parent, "circle", common)

    triangle_height = radius * 1.9
    points = [
        (x, y - triangle_height * 0.62),
        (x - radius, y + triangle_height * 0.38),
        (x + radius, y + triangle_height * 0.38),
    ]
    common["points"] = " ".join(
        "%s,%s" % (format_number(px), format_number(py))
        for px, py in points)
    return svg_child(parent, "polygon", common)


def add_diamond(parent, x, y, radius, fill, css_class="median-point"):
    points = [
        (x, y - radius),
        (x + radius, y),
        (x, y + radius),
        (x - radius, y),
    ]
    return svg_child(parent, "polygon", {
        "points": " ".join(
            "%s,%s" % (format_number(px), format_number(py))
            for px, py in points),
        "class": css_class,
        "fill": fill,
    })


def format_number(value):
    return ("%.3f" % float(value)).rstrip("0").rstrip(".")


def nice_tick_step(span, target_ticks=7):
    if span <= 0:
        return 1.0
    rough_step = span / float(target_ticks)
    magnitude = 10.0 ** math.floor(math.log10(rough_step))
    for multiplier in (1.0, 2.0, 5.0, 10.0):
        candidate = multiplier * magnitude
        if candidate >= rough_step:
            return candidate
    return 10.0 * magnitude


def short_revision(revision):
    return revision[:7] if revision and revision != "unknown" else "unknown"


def parse_metadata_file(path, prefix):
    if not path or not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    return ""


def read_first_line(path):
    if not path or not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[0].strip() if lines else ""


def read_pairs(csv_path):
    rounds = {}
    expected_transactions = None
    with csv_path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = {
            "mode", "round", "order", "transactions", "ns_per_transaction"
        }
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            missing = sorted(required.difference(reader.fieldnames or []))
            raise ValueError("CSV is missing required columns: %s" % ", ".join(missing))

        for row in reader:
            mode = row["mode"]
            if mode not in ("enabled", "disabled"):
                raise ValueError("Unexpected mode: %s" % mode)
            round_number = int(row["round"])
            if round_number <= 0:
                raise ValueError("Round numbers must be positive")
            order = row["order"]
            if order not in ("enabled-first", "disabled-first"):
                raise ValueError("Unexpected execution order: %s" % order)
            transactions = int(row["transactions"])
            if transactions <= 0:
                raise ValueError("Transactions must be positive")
            if expected_transactions is None:
                expected_transactions = transactions
            elif expected_transactions != transactions:
                raise ValueError("All samples must use the same transaction count")
            latency_us = float(row["ns_per_transaction"]) / 1000.0
            if not math.isfinite(latency_us) or latency_us <= 0:
                raise ValueError("Latency values must be finite and positive")

            pair = rounds.setdefault(round_number, {"order": order})
            if pair["order"] != order:
                raise ValueError("A round has inconsistent execution-order labels")
            if mode in pair:
                raise ValueError("A round contains duplicate %s samples" % mode)
            pair[mode] = latency_us

    if not rounds:
        raise ValueError("CSV contains no samples")

    pairs = []
    for round_number in sorted(rounds):
        pair = rounds[round_number]
        if "enabled" not in pair or "disabled" not in pair:
            raise ValueError("Round %d is not a complete pair" % round_number)
        pair["round"] = round_number
        pair["difference"] = pair["enabled"] - pair["disabled"]
        pair["reduction_percent"] = (
            pair["difference"] / pair["enabled"] * 100.0)
        pairs.append(pair)

    return pairs, expected_transactions


def exact_median_interval(values, target_coverage=0.95):
    """Return the narrowest central order-statistic interval at target coverage."""
    ordered = sorted(values)
    count = len(ordered)
    selected_tail_count = 0
    selected_coverage = 1.0 - 2.0 / (2 ** count)
    for tail_count in range((count - 1) // 2 + 1):
        lower_tail_probability = sum(
            math.comb(count, index) for index in range(tail_count + 1)
        ) / float(2 ** count)
        coverage = 1.0 - 2.0 * lower_tail_probability
        if coverage >= target_coverage:
            selected_tail_count = tail_count
            selected_coverage = coverage
        else:
            break

    return (
        ordered[selected_tail_count],
        ordered[count - selected_tail_count - 1],
        selected_coverage,
    )


def render_figure(pairs, transactions, output_path, metadata):
    width = 1440
    height = 920
    root = svg_element("svg", {
        "width": str(width),
        "height": str(height),
        "viewBox": "0 0 %d %d" % (width, height),
        "role": "img",
        "aria-labelledby": "figure-title figure-description",
    })
    svg_child(root, "title", {"id": "figure-title"},
        "Intervention log payload cost in a phase-1 deny microbenchmark")

    enabled_values = [pair["enabled"] for pair in pairs]
    disabled_values = [pair["disabled"] for pair in pairs]
    differences = [pair["difference"] for pair in pairs]
    reductions = [pair["reduction_percent"] for pair in pairs]
    enabled_median = statistics.median(enabled_values)
    disabled_median = statistics.median(disabled_values)
    difference_median = statistics.median(differences)
    reduction_median = statistics.median(reductions)
    interval_low, interval_high, interval_coverage = exact_median_interval(
        differences)

    positive_count = sum(value > 0.0 for value in differences)
    negative_count = sum(value < 0.0 for value in differences)
    zero_count = len(differences) - positive_count - negative_count
    direction_description = (
        "%d positive, %d negative, and %d zero paired differences"
        % (positive_count, negative_count, zero_count)
    )

    description = (
        "%d paired rounds compare intervention payload enabled and disabled. "
        "The data contain %s. The paired median enabled-minus-disabled "
        "difference is %.3f microseconds per transaction, with an exact "
        "%.1f percent nonparametric interval from %.3f to %.3f microseconds."
    ) % (
        len(pairs), direction_description, difference_median,
        interval_coverage * 100.0, interval_low, interval_high)
    svg_child(root, "desc", {"id": "figure-description"}, description)

    metadata_text = (
        "source=%s; checkout=%s; run=%s; pairs=%d; transactions_per_sample=%d"
        % (metadata["source_commit"], metadata["checkout_commit"],
           metadata["run_url"], len(pairs), transactions)
    )
    svg_child(root, "metadata", text=metadata_text)

    style = """
        text {
            font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\",
                Helvetica, Arial, sans-serif;
            fill: #17212B;
        }
        .title { font-size: 28px; font-weight: 600; letter-spacing: -0.2px; }
        .subtitle { font-size: 16px; fill: #596675; }
        .panel-title { font-size: 18px; font-weight: 600; }
        .panel-note { font-size: 14px; fill: #596675; }
        .tick { font-size: 13px; fill: #596675; }
        .axis-label { font-size: 14px; font-weight: 600; }
        .category { font-size: 15px; font-weight: 600; }
        .category-detail { font-size: 14px; fill: #596675; }
        .round-label { font-size: 12px; fill: #596675; }
        .summary-label { font-size: 14px; font-weight: 600; }
        .summary-value { font-size: 15px; font-weight: 600; }
        .legend { font-size: 13px; fill: #596675; }
        .footnote { font-size: 13.5px; fill: #596675; }
        .axis { stroke: #17212B; stroke-width: 1.2; shape-rendering: crispEdges; }
        .grid { stroke: #DCE3E9; stroke-width: 1; shape-rendering: crispEdges; }
        .zero { stroke: #596675; stroke-width: 1.5; shape-rendering: crispEdges; }
        .pair-line { stroke: #8793A1; stroke-width: 1.5; stroke-opacity: 0.55; }
        .effect-line { stroke: #009E73; stroke-width: 1.5; stroke-opacity: 0.35; }
        .point { stroke: #FFFFFF; stroke-width: 1.5; }
        .median-point { stroke: #17212B; stroke-width: 1.5; }
        .interval { stroke: #17212B; stroke-width: 2.5; }
        .header-rule { stroke: #DCE3E9; stroke-width: 1; }
    """
    svg_child(root, "style", text=style)
    svg_child(root, "rect", {
        "x": "0", "y": "0", "width": str(width), "height": str(height),
        "fill": COLORS["background"],
    })

    add_text(root, 64, 52,
        "Intervention log payload cost in a phase-1 deny microbenchmark",
        "title")
    add_text(root, 64, 82,
        "Paired Linux release benchmark · elapsed time per transaction · lower is better",
        "subtitle")

    add_text(root, 986, 82, "Run order:", "legend")
    add_marker(root, 1080, 77, "enabled-first", COLORS["pair"], 5.5)
    add_text(root, 1094, 82, "Enabled first", "legend")
    add_marker(root, 1223, 77, "disabled-first", COLORS["pair"], 5.5)
    add_text(root, 1237, 82, "Disabled first", "legend")
    add_line(root, 64, 108, 1376, 108, "header-rule")

    add_text(root, 64, 150, "A", "panel-title")
    add_text(root, 91, 150, "Observed elapsed time", "panel-title")
    add_text(root, 825, 150, "B", "panel-title")
    add_text(root, 852, 150, "Paired elapsed-time difference", "panel-title")
    add_text(root, 852, 176,
        "Enabled − disabled; positive means enabled took longer",
        "panel-note")

    a_left = 125.0
    a_right = 690.0
    a_top = 188.0
    a_bottom = 646.0
    enabled_x = 300.0
    disabled_x = 560.0
    latency_max = max(25.0, math.ceil(max(enabled_values) / 5.0) * 5.0)

    def latency_y(value):
        return a_bottom - value / latency_max * (a_bottom - a_top)

    for tick in range(0, int(latency_max) + 1, 5):
        y = latency_y(float(tick))
        add_line(root, a_left, y, a_right, y, "grid")
        add_text(root, a_left - 13, y + 4, str(tick), "tick", "end")
    add_line(root, a_left, a_top, a_left, a_bottom, "axis")
    add_line(root, a_left, a_bottom, a_right, a_bottom, "axis")
    add_text(root, 35, (a_top + a_bottom) / 2.0,
        "Elapsed time per transaction (µs)", "axis-label", "middle",
        "rotate(-90 35 %s)" % format_number((a_top + a_bottom) / 2.0))

    for pair in pairs:
        enabled_y = latency_y(pair["enabled"])
        disabled_y = latency_y(pair["disabled"])
        add_line(root, enabled_x, enabled_y, disabled_x, disabled_y,
            "pair-line")
    for pair in pairs:
        add_marker(root, enabled_x, latency_y(pair["enabled"]),
            pair["order"], COLORS["enabled"])
        add_marker(root, disabled_x, latency_y(pair["disabled"]),
            pair["order"], COLORS["disabled"])

    add_diamond(root, enabled_x, latency_y(enabled_median), 9,
        COLORS["enabled"])
    add_diamond(root, disabled_x, latency_y(disabled_median), 9,
        COLORS["disabled"])

    add_text(root, enabled_x, 684, "Payload enabled", "category", "middle")
    add_text(root, enabled_x, 708, "Median %.2f µs" % enabled_median,
        "category-detail", "middle")
    add_text(root, disabled_x, 684, "Payload disabled", "category", "middle")
    add_text(root, disabled_x, 708, "Median %.2f µs" % disabled_median,
        "category-detail", "middle")
    add_text(root, (a_left + a_right) / 2.0, 754,
        "Condition", "axis-label", "middle")

    b_left = 825.0
    b_right = 1350.0
    b_top = 207.0
    b_bottom = 548.0
    b_axis_y = 704.0
    observed_min = min(0.0, min(differences))
    observed_max = max(0.0, max(differences))
    tick_step = nice_tick_step(observed_max - observed_min)
    effect_min = 0.0 if observed_min >= 0.0 else math.floor(
        (observed_min - tick_step * 0.35) / tick_step) * tick_step
    effect_max = 0.0 if observed_max <= 0.0 else math.ceil(
        (observed_max + tick_step * 0.35) / tick_step) * tick_step
    if effect_min == effect_max:
        effect_max = effect_min + tick_step

    def effect_x(value):
        return b_left + (value - effect_min) / (effect_max - effect_min) \
            * (b_right - b_left)

    first_tick = int(math.ceil(effect_min / tick_step - 1e-9))
    last_tick = int(math.floor(effect_max / tick_step + 1e-9))
    for tick_index in range(first_tick, last_tick + 1):
        tick = tick_index * tick_step
        x = effect_x(tick)
        add_line(root, x, b_top - 11, x, b_axis_y, "grid")
        add_text(root, x, b_axis_y + 23, format_number(tick), "tick", "middle")
    add_line(root, effect_x(0.0), b_top - 11, effect_x(0.0), b_axis_y,
        "zero")
    add_line(root, b_left, b_axis_y, b_right, b_axis_y, "axis")

    row_step = (b_bottom - b_top) / max(1, len(pairs) - 1)
    for index, pair in enumerate(pairs):
        y = b_top + index * row_step
        x = effect_x(pair["difference"])
        add_text(root, b_left - 16, y + 4, "R%d" % pair["round"],
            "round-label", "end")
        add_line(root, effect_x(0.0), y, x, y, "effect-line")
        add_marker(root, x, y, pair["order"], COLORS["effect"], 6.0)

    summary_y = 633.0
    interval_x_low = effect_x(interval_low)
    interval_x_high = effect_x(interval_high)
    median_x = effect_x(difference_median)
    add_text(root, b_left - 16, summary_y + 5, "Median", "summary-label", "end")
    add_line(root, interval_x_low, summary_y, interval_x_high, summary_y,
        "interval")
    add_line(root, interval_x_low, summary_y - 8, interval_x_low,
        summary_y + 8, "interval")
    add_line(root, interval_x_high, summary_y - 8, interval_x_high,
        summary_y + 8, "interval")
    add_diamond(root, median_x, summary_y, 9, COLORS["effect"])
    add_text(root, median_x, summary_y + 35,
        "%.2f µs  [%.2f, %.2f]" % (
            difference_median, interval_low, interval_high),
        "summary-value", "middle")
    if interval_coverage >= 0.95:
        interval_caption = "median · exact %.1f%% within-run CI" \
            % (interval_coverage * 100.0)
    else:
        interval_caption = "median · exact %.1f%% within-run interval" \
            % (interval_coverage * 100.0)
    add_text(root, median_x, summary_y + 56,
        interval_caption, "round-label", "middle")
    add_text(root, (b_left + b_right) / 2.0, 754,
        "Enabled − disabled elapsed time (µs / transaction)",
        "axis-label", "middle")

    total_transactions = len(pairs) * 2 * transactions
    add_text(root, 64, 816,
        ("%d paired sample aggregates · %s transactions/sample · %s/mode · "
         "%s timed total · alternating execution order")
        % (len(pairs), format_integer(transactions),
           format_integer(len(pairs) * transactions),
           format_integer(total_transactions)),
        "footnote")
    direction_counts = "%d positive · %d negative" % (
        positive_count, negative_count)
    if zero_count:
        direction_counts += " · %d zero" % zero_count
    add_text(root, 64, 847,
        ("Paired median enabled − disabled: %.2f µs; median paired relative "
         "difference: %.2f%% · %s.")
        % (difference_median, reduction_median, direction_counts),
        "footnote")
    if interval_coverage >= 0.95:
        coverage_note = "closest available ≥95%"
    else:
        coverage_note = "maximum attainable with n=%d" % len(pairs)
    add_text(root, 64, 878,
        ("Within-run exact interval: %.1f%% [%.2f, %.2f] µs (%s); assumes "
         "independent paired rounds, which are the statistical units.")
        % (interval_coverage * 100.0, interval_low, interval_high,
           coverage_note),
        "footnote")

    environment = metadata["environment"] or "environment not recorded"
    run_label = metadata["run_url"].rstrip("/").split("/")[-1]
    revision_line = (
        "Actions run %s · source %s · %s · descriptive for this fixture; "
        "not a cross-host or cross-workload estimate."
        % (run_label, short_revision(metadata["source_commit"]),
           environment)
    )
    add_text(root, 64, 909, revision_line, "footnote")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    if hasattr(ET, "indent"):
        ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    with output_path.open("ab") as stream:
        stream.write(b"\n")


def format_integer(value):
    return format(int(value), ",")


def build_metadata(arguments):
    metadata_directory = arguments.metadata_directory
    source_commit = arguments.source_commit
    checkout_commit = arguments.checkout_commit
    cpu = ""
    compiler = ""

    if metadata_directory:
        source_commit = source_commit or read_first_line(
            metadata_directory / "source-commit.txt")
        checkout_commit = checkout_commit or read_first_line(
            metadata_directory / "checkout-commit.txt")
        if not checkout_commit:
            checkout_commit = read_first_line(metadata_directory / "commit.txt")
        cpu = parse_metadata_file(metadata_directory / "lscpu.txt", "Model name:")
        cpu_count = parse_metadata_file(metadata_directory / "lscpu.txt", "CPU(s):")
        compiler_line = read_first_line(metadata_directory / "compiler.txt")
        if compiler_line:
            version = re.search(r"(\d+\.\d+(?:\.\d+)?)$", compiler_line)
            compiler = "GCC %s" % version.group(1) if version else compiler_line
        if cpu and cpu_count:
            cpu = cpu.replace("INTEL(R)", "Intel").replace("XEON(R)", "Xeon")
            cpu = re.sub(r"\s+\d+-Core Processor$", "", cpu)
            cpu = "%s (%s vCPU)" % (cpu, cpu_count)

    environment_parts = []
    if arguments.environment_label:
        environment_parts.append(arguments.environment_label)
    if cpu:
        environment_parts.append(cpu)
    if compiler:
        environment_parts.append(compiler)
    if arguments.build_label:
        environment_parts.append(arguments.build_label)

    return {
        "run_url": arguments.run_url or "unpublished-run",
        "source_commit": source_commit or "unknown",
        "checkout_commit": checkout_commit or "unknown",
        "environment": " · ".join(environment_parts),
    }


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Render a paired intervention-log benchmark as SVG")
    parser.add_argument("--input", required=True, type=Path,
        help="Benchmark CSV produced by intervention_log_benchmark")
    parser.add_argument("--output", required=True, type=Path,
        help="Destination SVG path")
    parser.add_argument("--metadata-directory", type=Path,
        help="Directory containing Actions runner metadata files")
    parser.add_argument("--run-url", default="",
        help="URL of the Actions run that produced the samples")
    parser.add_argument("--source-commit", default="",
        help="Source branch revision represented by the samples")
    parser.add_argument("--checkout-commit", default="",
        help="Exact revision checked out by the Actions job")
    parser.add_argument("--environment-label", default="",
        help="Short operating-system label")
    parser.add_argument("--build-label", default="",
        help="Short compiler/build configuration label")
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    pairs, transactions = read_pairs(arguments.input)
    metadata = build_metadata(arguments)
    render_figure(pairs, transactions, arguments.output, metadata)
    print("Rendered %s from %d paired rounds" % (arguments.output, len(pairs)))


if __name__ == "__main__":
    main()
