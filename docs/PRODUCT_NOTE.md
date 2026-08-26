# Product Note

## Product purpose

ParcelPilot customers need clear answers about shipments, cancellations, service credits, account features, product issues and support SLAs. A correct answer may require both customer records and several documents.

The project provides one complete customer-facing chatbot. Each signed-in customer can access only their own account information.

## Additional client problem: Trust and Reliability

The selected additional problem is **Trust and Reliability**.

This problem is important because a fluent answer is not useful when it applies an old policy, ignores an agreement, exposes another customer's data or performs an action without approval.

Trust and Reliability is addressed through:

- customer authentication and backend account isolation;
- secure password hashing;
- fixed source precedence;
- removal of deprecated documents from current answers;
- detection of conflicts with historical ticket resolutions;
- exact fee, service-credit and SLA calculations;
- semantic search over authorized documents;
- evidence citations and visible tool activity;
- Pydantic validation of LLM plans and answers;
- clear uncertainty and verification messages;
- an evidence-based safe response when the LLM service is unavailable; and
- explicit confirmation before an escalation or follow-up is executed.

For example, the old resolution in `TKT-451` treats 3,000 rows as the product limit. The current product guide states that 5,000 rows are supported. Files below 3,000 rows are a temporary workaround for known issue `KI-208`. The chatbot uses the current product guide and explains the difference.

## Prioritized product roadmap

### Priority 1: Live identity and customer data

Company SSO and live order, ticket and carrier connections would provide current production data and verified customer identities.

### Priority 2: Durable production services

A managed database, durable action queue and operational monitoring would support higher traffic, backups and reliable background work.

### Priority 3: Human support handoff

A Zendesk or Jira connection could create a support handoff containing verified facts, calculations, uncertainty and citations. Customer and support-agent feedback could be added to the evaluation suite.

### Priority 4: Proactive issue detection

With enough historical support data, semantic ticket clustering and time-series analysis could identify repeated complaints, emerging incidents and SLA risks.

## Submission scope

The submission uses the synthetic accounts, supplied workbook, supplied PDFs and local assessment actions. Production SSO, live ParcelPilot services, external ticketing systems and live carrier APIs require company systems and credentials, so they are planned as production integrations rather than assessment dependencies.

This scope keeps the project complete, testable and reproducible with the provided assessment data.

## Success metric

The main metric is **verified self-service resolution rate**:

> The percentage of customer conversations completed without human support and later verified as correct by the available evidence.

This metric measures both usefulness and accuracy. Cross-account data exposure is tracked separately as a zero-tolerance safety metric.
