/*
 * ModSecurity, http://www.modsecurity.org/
 * Copyright (c) 2015 - 2023 Trustwave Holdings, Inc. (http://www.trustwave.com/)
 *
 * You may not use this file except in compliance with
 * the License.  You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * If any of the files related to licensing are missing or if you have any
 * other questions related to licensing please contact Trustwave Holdings, Inc.
 * directly using the email address security@modsecurity.org.
 *
 */

#ifndef SRC_INTERVENTION_LOG_H_
#define SRC_INTERVENTION_LOG_H_

#include <string.h>

#include "modsecurity/modsecurity.h"
#include "modsecurity/rule_message.h"
#include "modsecurity/transaction.h"

namespace modsecurity {

namespace intervention {

static inline void setLog(Transaction *transaction,
    const RuleMessage &message) {
    freeLog(&transaction->m_it);
    if (!transaction->m_ms->isInterventionLogEnabled()) {
        return;
    }

    transaction->m_it.log = strdup(
        message.log(RuleMessage::LogMessageInfo::ClientLogMessageInfo).c_str());
}


static inline void setLog(Transaction *transaction, const char *message) {
    freeLog(&transaction->m_it);
    if (!transaction->m_ms->isInterventionLogEnabled()) {
        return;
    }

    transaction->m_it.log = strdup(message);
}

}  // namespace intervention
}  // namespace modsecurity

#endif  // SRC_INTERVENTION_LOG_H_
