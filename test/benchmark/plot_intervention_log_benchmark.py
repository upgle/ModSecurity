#!/usr/bin/env python3
# ModSecurity, http://www.modsecurity.org/
# Copyright (c) 2026 OWASP ModSecurity Project
#
# You may not use this file except in compliance with
# the License. You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# If any of the files related to licensing are missing or if you have any
# other questions related to licensing, please contact OWASP directly using
# the email address modsecurity@owasp.org.

"""Render a publication-style paired benchmark figure with Matplotlib."""

import argparse
import csv
import io
import math
import re
import statistics
from pathlib import Path
from xml.etree import ElementTree as ET

import matplotlib

matplotlib.use("svg")

from matplotlib import pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402


SVG_NAMESPACE = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NAMESPACE)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
ET.register_namespace("dc", "http://purl.org/dc/elements/1.1/")
ET.register_namespace("cc", "http://creativecommons.org/ns#")
ET.register_namespace("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#")

COLORS = {
    "text": "#222222",
    "muted": "#666666",
    "grid": "#D9D9D9",
    "pair": "#BDBDBD",
    "enabled": "#595959",
    "disabled": "#2C7FB8",
}

ORDER_MARKERS = {
    "enabled-first": "o",
    "disabled-first": "^",
}


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
            if order not in ORDER_MARKERS:
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
    """Return the narrowest central order-statistic median interval."""
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


def configure_matplotlib():
    matplotlib.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans"],
        "font.size": 8.0,
        "axes.titlesize": 8.5,
        "axes.titleweight": "semibold",
        "axes.labelsize": 8.0,
        "axes.labelcolor": COLORS["text"],
        "axes.edgecolor": COLORS["text"],
        "axes.linewidth": 0.65,
        "axes.axisbelow": True,
        "xtick.labelsize": 7.5,
        "ytick.labelsize": 7.5,
        "xtick.color": COLORS["text"],
        "ytick.color": COLORS["text"],
        "xtick.major.size": 2.5,
        "ytick.major.size": 2.5,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "legend.fontsize": 6.8,
        "lines.solid_capstyle": "round",
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
        "svg.fonttype": "none",
        "svg.hashsalt": "modsecurity-intervention-log-benchmark",
    })


def style_axis(axis):
    axis.spines["top"].set_visible(False)
    axis.spines["right"].set_visible(False)


def add_accessibility_metadata(svg_bytes, pairs, statistics_summary, metadata):
    root = ET.fromstring(svg_bytes)
    title_id = "figure-title"
    description_id = "figure-description"
    root.set("role", "img")
    root.set("aria-labelledby", "%s %s" % (title_id, description_id))
    root.set("data-source-commit", metadata["source_commit"])
    root.set("data-checkout-commit", metadata["checkout_commit"])
    root.set("data-run-url", metadata["run_url"])
    root.set("data-environment", metadata["environment"])
    root.set("data-matplotlib-version", matplotlib.__version__)

    positive_count = sum(pair["difference"] > 0.0 for pair in pairs)
    title = root.find("{%s}title" % SVG_NAMESPACE)
    if title is None:
        title = ET.Element("{%s}title" % SVG_NAMESPACE)
    else:
        root.remove(title)
    title.set("id", title_id)
    title.text = "Paired phase-1 deny benchmark"
    description = ET.Element(
        "{%s}desc" % SVG_NAMESPACE, {"id": description_id})
    description.text = (
        "%d paired round aggregates compare intervention log payload enabled "
        "and disabled. Payload disabled had a lower sample mean in %d rounds. "
        "The median enabled-minus-disabled difference was %.3f microseconds "
        "per transaction; its exact %.1f percent order-statistic interval was "
        "%.3f to %.3f microseconds."
        % (
            len(pairs),
            positive_count,
            statistics_summary["difference_median"],
            statistics_summary["interval_coverage"] * 100.0,
            statistics_summary["interval_low"],
            statistics_summary["interval_high"],
        )
    )
    root.insert(0, title)
    root.insert(1, description)

    tree = ET.ElementTree(root)
    if hasattr(ET, "indent"):
        ET.indent(tree, space="  ")
    output = io.BytesIO()
    tree.write(output, encoding="utf-8", xml_declaration=True)
    output.write(b"\n")
    return output.getvalue()


def render_figure(pairs, transactions, output_path, metadata):
    configure_matplotlib()

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

    statistics_summary = {
        "difference_median": difference_median,
        "interval_low": interval_low,
        "interval_high": interval_high,
        "interval_coverage": interval_coverage,
    }

    figure, time_axis = plt.subplots(figsize=(5.8, 3.65))
    figure.subplots_adjust(
        left=0.14, right=0.97, bottom=0.26, top=0.82)

    for pair in pairs:
        time_axis.plot(
            (0.0, 1.0),
            (pair["enabled"], pair["disabled"]),
            color=COLORS["pair"],
            linewidth=0.65,
            zorder=1,
        )

    for order, marker in ORDER_MARKERS.items():
        selected = [pair for pair in pairs if pair["order"] == order]
        time_axis.scatter(
            [0.0] * len(selected),
            [pair["enabled"] for pair in selected],
            marker=marker,
            s=24,
            facecolor=COLORS["enabled"],
            edgecolor="white",
            linewidth=0.45,
            zorder=2,
        )
        time_axis.scatter(
            [1.0] * len(selected),
            [pair["disabled"] for pair in selected],
            marker=marker,
            s=24,
            facecolor=COLORS["disabled"],
            edgecolor="white",
            linewidth=0.45,
            zorder=2,
        )

    for x_value, median_value, label_offset, alignment in (
        (0.0, enabled_median, -0.19, "right"),
        (1.0, disabled_median, 0.19, "left"),
    ):
        time_axis.plot(
            (x_value - 0.13, x_value + 0.13),
            (median_value, median_value),
            color=COLORS["text"],
            linewidth=1.35,
            zorder=3,
        )
        time_axis.text(
            x_value + label_offset,
            median_value,
            "%.2f" % median_value,
            ha=alignment,
            va="center",
            fontsize=7.0,
            color=COLORS["text"],
        )

    latency_max = max(25.0, math.ceil(max(enabled_values) / 5.0) * 5.0)
    time_axis.set_xlim(-0.4, 1.4)
    time_axis.set_ylim(0.0, latency_max)
    time_axis.set_xticks((0.0, 1.0), ("Payload enabled", "Payload disabled"))
    time_axis.set_yticks(
        [float(value) for value in range(0, int(latency_max) + 1, 5)])
    time_axis.set_ylabel("Sample mean time (µs/transaction)")
    time_axis.set_title("Phase-1 deny processing time", loc="left", pad=7.0)
    time_axis.grid(axis="y", color=COLORS["grid"], linewidth=0.45)
    time_axis.tick_params(axis="x", length=0, pad=5)
    style_axis(time_axis)

    legend_handles = [
        Line2D(
            [], [],
            linestyle="none",
            marker=marker,
            markersize=4.2,
            markerfacecolor=COLORS["text"],
            markeredgecolor="white",
            markeredgewidth=0.45,
            label=label,
        )
        for marker, label in (
            ("o", "enabled measured first"),
            ("^", "disabled measured first"),
        )
    ]
    figure.legend(
        handles=legend_handles,
        loc="upper right",
        bbox_to_anchor=(0.99, 0.985),
        frameon=False,
        ncol=2,
        handletextpad=0.35,
        columnspacing=0.9,
        borderaxespad=0.0,
    )
    figure.text(
        0.5,
        0.115,
        ("Median paired difference (enabled − disabled): "
         "%.2f µs/transaction") % difference_median,
        ha="center",
        va="center",
        fontsize=7.3,
        color=COLORS["text"],
    )
    figure.text(
        0.5,
        0.06,
        ("Exact within-run 95%% CI: %.2f–%.2f µs/transaction · "
         "n=%d paired rounds") % (
            interval_low, interval_high, len(pairs)),
        ha="center",
        va="center",
        fontsize=6.8,
        color=COLORS["muted"],
    )

    description = (
        "%d paired rounds; %s transactions per aggregate. Median paired "
        "difference %.3f microseconds per transaction; exact %.1f percent "
        "interval %.3f to %.3f microseconds."
        % (
            len(pairs),
            format(int(transactions), ","),
            difference_median,
            interval_coverage * 100.0,
            interval_low,
            interval_high,
        )
    )
    svg_buffer = io.BytesIO()
    figure.savefig(
        svg_buffer,
        format="svg",
        metadata={
            "Title": "Paired phase-1 deny benchmark",
            "Description": description,
            "Creator": "Matplotlib %s" % matplotlib.__version__,
            "Date": None,
        },
    )
    plt.close(figure)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(add_accessibility_metadata(
        svg_buffer.getvalue(), pairs, statistics_summary, metadata))

    return {
        "enabled_median": enabled_median,
        "disabled_median": disabled_median,
        "difference_median": difference_median,
        "reduction_median": reduction_median,
        "interval_low": interval_low,
        "interval_high": interval_high,
        "interval_coverage": interval_coverage,
    }


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
    if arguments.output.suffix.lower() != ".svg":
        raise ValueError("The output path must use the .svg extension")
    pairs, transactions = read_pairs(arguments.input)
    metadata = build_metadata(arguments)
    summary = render_figure(pairs, transactions, arguments.output, metadata)
    print(
        "Rendered %s from %d paired rounds with Matplotlib %s "
        "(median difference %.3f µs)"
        % (
            arguments.output,
            len(pairs),
            matplotlib.__version__,
            summary["difference_median"],
        )
    )


if __name__ == "__main__":
    main()
