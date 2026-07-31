/*
 * ModSecurity, http://www.modsecurity.org/
 * Copyright (c) 2026 OWASP ModSecurity Project
 *
 * You may not use this file except in compliance with
 * the License.  You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * If any of the files related to licensing are missing or if you have any
 * other questions related to licensing, please contact OWASP directly using
 * the email address modsecurity@owasp.org.
 *
 */

#include <errno.h>
#include <stdint.h>
#include <stdlib.h>

#include <algorithm>
#include <chrono>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>

#include "modsecurity/intervention.h"
#include "modsecurity/modsecurity.h"
#include "modsecurity/rules_set.h"
#include "modsecurity/transaction.h"

namespace {

constexpr unsigned long long kDefaultIterations = 50000;
constexpr unsigned long long kDefaultRounds = 12;

const char kRules[] =
    "SecRuleEngine On\n"
    "SecAction \"id:900001,phase:1,deny,status:403,nolog,"
    "severity:'CRITICAL',tag:'performance',"
    "msg:'Representative phase-one intervention payload used to measure "
    "connector-side formatting and allocation costs'\"\n";

volatile uint64_t observable_result = 0;

struct Sample {
    bool enabled;
    unsigned long long round;
    const char *order;
    uint64_t elapsed_ns;
    double ns_per_transaction;
    double transactions_per_second;
};

bool parse_positive_integer(const char *value, unsigned long long *result) {
    if (value[0] == '-') {
        return false;
    }
    char *end = nullptr;
    errno = 0;
    const unsigned long long parsed = strtoull(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0' || parsed == 0) {
        return false;
    }
    *result = parsed;
    return true;
}

Sample run_sample(modsecurity::ModSecurity *modsec,
    modsecurity::RulesSet *rules, bool enabled,
    unsigned long long iterations, unsigned long long round,
    const char *order) {
    modsec->setInterventionLogPayloadEnabled(enabled);

    uint64_t sample_observable = 0;
    const auto start = std::chrono::steady_clock::now();
    for (unsigned long long i = 0; i < iterations; ++i) {
        modsecurity::Transaction transaction(modsec, rules, nullptr);
        transaction.processConnection("192.0.2.1", 12345,
            "192.0.2.2", 80);
        transaction.processURI("/benchmark", "GET", "1.1");
        transaction.addRequestHeader("Host", "benchmark.example");
        transaction.processRequestHeaders();

        modsecurity::ModSecurityIntervention intervention;
        modsecurity::intervention::clean(&intervention);
        const bool intervention_seen = transaction.intervention(&intervention);
        const bool log_present = intervention.log != nullptr;

        if (!intervention_seen || intervention.status != 403
            || log_present != enabled) {
            std::cerr << "Unexpected intervention result for "
                << (enabled ? "enabled" : "disabled") << " mode"
                << std::endl;
            modsecurity::msc_intervention_cleanup(&intervention);
            exit(EXIT_FAILURE);
        }

        sample_observable += static_cast<uint64_t>(intervention.status);
        sample_observable += static_cast<uint64_t>(log_present);
        modsecurity::msc_intervention_cleanup(&intervention);
    }
    const auto finish = std::chrono::steady_clock::now();
    observable_result += sample_observable;

    const uint64_t elapsed_ns = std::chrono::duration_cast<
        std::chrono::nanoseconds>(finish - start).count();
    const double ns_per_transaction = static_cast<double>(elapsed_ns)
        / static_cast<double>(iterations);

    return {
        enabled,
        round,
        order,
        elapsed_ns,
        ns_per_transaction,
        1000000000.0 / ns_per_transaction,
    };
}

double median(std::vector<double> values) {
    std::sort(values.begin(), values.end());
    const size_t middle = values.size() / 2;
    if (values.size() % 2 != 0) {
        return values[middle];
    }
    return (values[middle - 1] + values[middle]) / 2.0;
}

void write_csv(const std::string &path, const std::vector<Sample> &samples,
    unsigned long long iterations) {
    std::ofstream output(path);
    if (!output) {
        std::cerr << "Failed to open benchmark output: " << path << std::endl;
        exit(EXIT_FAILURE);
    }

    output << "mode,round,order,transactions,elapsed_ns,"
        "ns_per_transaction,transactions_per_second\n";
    output << std::fixed << std::setprecision(3);
    for (const auto &sample : samples) {
        output << (sample.enabled ? "enabled" : "disabled") << ','
            << sample.round << ',' << sample.order << ',' << iterations << ','
            << sample.elapsed_ns << ',' << sample.ns_per_transaction << ','
            << sample.transactions_per_second << '\n';
    }
}

}  // namespace

int main(int argc, const char *argv[]) {
    unsigned long long iterations = kDefaultIterations;
    unsigned long long rounds = kDefaultRounds;
    std::string csv_path = "intervention-log-benchmark.csv";

    if (argc > 4
        || (argc > 1 && !parse_positive_integer(argv[1], &iterations))
        || (argc > 2 && !parse_positive_integer(argv[2], &rounds))
        || rounds % 2 != 0) {
        std::cerr << "Usage: " << argv[0]
            << " [iterations [even-rounds [csv-output]]]" << std::endl;
        return EXIT_FAILURE;
    }
    if (argc > 3) {
        csv_path = argv[3];
    }

    modsecurity::ModSecurity modsec;
    modsec.setConnectorInformation(
        "ModSecurity intervention log benchmark v1");

    modsecurity::RulesSet rules;
    if (rules.load(kRules) < 0) {
        std::cerr << "Failed to load benchmark rules: "
            << rules.getParserError() << std::endl;
        return EXIT_FAILURE;
    }

    const unsigned long long warmup_iterations =
        std::min<unsigned long long>(iterations, 5000);
    run_sample(&modsec, &rules, true, warmup_iterations, 0, "warmup");
    run_sample(&modsec, &rules, false, warmup_iterations, 0, "warmup");

    std::vector<Sample> samples;
    samples.reserve(rounds * 2);
    for (unsigned long long round = 1; round <= rounds; ++round) {
        if (round % 2 != 0) {
            samples.push_back(run_sample(&modsec, &rules, true, iterations,
                round, "enabled-first"));
            samples.push_back(run_sample(&modsec, &rules, false, iterations,
                round, "enabled-first"));
        } else {
            samples.push_back(run_sample(&modsec, &rules, false, iterations,
                round, "disabled-first"));
            samples.push_back(run_sample(&modsec, &rules, true, iterations,
                round, "disabled-first"));
        }
    }

    std::vector<double> enabled_ns;
    std::vector<double> disabled_ns;
    std::vector<double> paired_saved_ns;
    std::vector<double> paired_reduction_percent;
    enabled_ns.reserve(rounds);
    disabled_ns.reserve(rounds);
    paired_saved_ns.reserve(rounds);
    paired_reduction_percent.reserve(rounds);
    for (const auto &sample : samples) {
        (sample.enabled ? enabled_ns : disabled_ns).push_back(
            sample.ns_per_transaction);
    }
    for (size_t i = 0; i < samples.size(); i += 2) {
        const Sample &first = samples[i];
        const Sample &second = samples[i + 1];
        const double enabled = first.enabled
            ? first.ns_per_transaction : second.ns_per_transaction;
        const double disabled = first.enabled
            ? second.ns_per_transaction : first.ns_per_transaction;
        const double saved = enabled - disabled;
        paired_saved_ns.push_back(saved);
        paired_reduction_percent.push_back(saved / enabled * 100.0);
    }

    write_csv(csv_path, samples, iterations);

    const double enabled_median = median(enabled_ns);
    const double disabled_median = median(disabled_ns);
    const double paired_saved_median = median(paired_saved_ns);
    const double paired_reduction_median = median(paired_reduction_percent);

    std::cout << "## Intervention log paired benchmark\n\n"
        << "- Iterations per sample: " << iterations << "\n"
        << "- Paired rounds: " << rounds << "\n"
        << std::fixed << std::setprecision(2)
        << "- Payload enabled median: " << enabled_median << " ns/transaction\n"
        << "- Payload disabled median: " << disabled_median
        << " ns/transaction\n"
        << "- Paired median time avoided: " << paired_saved_median
        << " ns/transaction\n"
        << "- Paired median reduction: " << paired_reduction_median << "%\n"
        << "- Raw samples: `" << csv_path << "`\n";

    return observable_result == 0 ? EXIT_FAILURE : EXIT_SUCCESS;
}
